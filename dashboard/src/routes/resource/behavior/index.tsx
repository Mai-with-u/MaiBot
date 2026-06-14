import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { PointerEvent, ReactNode } from 'react'
import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  GitBranch,
  Loader2,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type Node,
  type NodeProps,
  type NodeTypes,
} from 'reactflow'

import 'reactflow/dist/style.css'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Tabs, TabsContent } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import {
  listBehaviorClusters,
  type BehaviorGraphData,
  type BehaviorClusterItem,
  type BehaviorClusterTag,
  type BehaviorPathDetail,
  type BehaviorPathItem,
  type BehaviorRetrievalDebugPayload,
} from '@/lib/behavior-api'
import { cn } from '@/lib/utils'
import { useToast } from '@/hooks/use-toast'

import { useBehaviorChats } from './hooks/useBehaviorChats'
import { useBehaviorDebug } from './hooks/useBehaviorDebug'
import { useBehaviorGraph } from './hooks/useBehaviorGraph'
import { useBehaviorPathDetail } from './hooks/useBehaviorPathDetail'
import { useBehaviorPaths } from './hooks/useBehaviorPaths'

const PAGE_SIZE = 20

type ActiveTab = 'paths' | 'scene-browser' | 'scene-network' | 'tag-network' | 'debug' | 'graph'

interface BehaviorSceneGroup {
  key: string
  sceneClusterId: number | null
  clusterName: string
  clusterTags: BehaviorClusterTag[]
  clusterSourceCount: number
  chatName: string
  paths: BehaviorPathItem[]
  latestUpdate: string | null
  bestScore: number
  activationCount: number
  successCount: number
  failureCount: number
}

interface BehaviorFlowNodeData {
  label: string
  kind: string
  detail: string
}

interface BehaviorNetworkNodeData {
  label: string
  detail: string
  metric: string
  tone: 'primary' | 'teal' | 'amber' | 'violet' | 'slate'
}

type BehaviorFlowNode = Node<BehaviorFlowNodeData>
type BehaviorFlowEdge = Edge
type BehaviorNetworkNode = Node<BehaviorNetworkNodeData>
type BehaviorNetworkEdge = Edge

interface CanvasNetworkNode {
  id: string
  x: number
  y: number
  vx: number
  vy: number
  fixed: boolean
  radius: number
  label: string
  detail: string
  metric: string
  tone: BehaviorNetworkNodeData['tone']
}

interface CanvasTransform {
  x: number
  y: number
  zoom: number
}

interface CanvasNetworkSettings {
  showLabels: boolean
  paused: boolean
  search: string
  minWeightValue: number
  nodeLimit: number
  onlyConnected: boolean
}

type CanvasNetworkKind = 'scene' | 'tag'

type BehaviorPathSortBy =
  | 'activation_count'
  | 'count'
  | 'failure_count'
  | 'last_active_time'
  | 'last_feedback_time'
  | 'scene_cluster_source_count'
  | 'score'
  | 'success_count'
  | 'update_time'

type SortOrder = 'asc' | 'desc'
type BehaviorClusterGroupMode = 'count' | 'none'
type BehaviorClusterSortBy =
  | 'activation_count'
  | 'path_count'
  | 'source_count'
  | 'update_time'

const DEFAULT_CANVAS_NETWORK_SETTINGS: CanvasNetworkSettings = {
  showLabels: true,
  paused: false,
  search: '',
  minWeightValue: 0,
  nodeLimit: 0,
  onlyConnected: true,
}

const PATH_SORT_LABELS: Record<BehaviorPathSortBy, string> = {
  activation_count: '使用次数',
  count: '学习次数',
  failure_count: '负向反馈',
  last_active_time: '最近使用',
  last_feedback_time: '最近反馈',
  scene_cluster_source_count: '场景样本',
  score: '路径分数',
  success_count: '正向反馈',
  update_time: '最近更新',
}

const CLUSTER_SORT_LABELS: Record<BehaviorClusterSortBy, string> = {
  update_time: '最近更新',
  path_count: '路径数量',
  source_count: '学习样本',
  activation_count: '使用次数',
}

const BehaviorGraphNode = memo(({ data }: NodeProps<BehaviorFlowNodeData>) => {
  const styleByKind: Record<string, string> = {
    action: 'border-emerald-300 bg-emerald-500 text-white shadow-[0_10px_28px_rgba(16,185,129,0.2)]',
    outcome: 'border-sky-300 bg-sky-500 text-white shadow-[0_10px_28px_rgba(14,165,233,0.2)]',
    path: 'border-violet-300 bg-violet-500 text-white shadow-[0_10px_28px_rgba(139,92,246,0.2)]',
  }
  const className =
    styleByKind[data.kind] ??
    'border-slate-300 bg-slate-700 text-white shadow-[0_10px_24px_rgba(15,23,42,0.16)]'

  return (
    <div className={cn('w-56 rounded-lg border px-3 py-2 text-left', className)}>
      <Handle className="opacity-0" type="target" position={Position.Left} />
      <div className="mb-1 text-[11px] font-medium uppercase opacity-75">{data.kind}</div>
      <div className="line-clamp-3 text-xs font-semibold leading-5" title={data.detail}>
        {data.label}
      </div>
      <Handle className="opacity-0" type="source" position={Position.Right} />
    </div>
  )
})

BehaviorGraphNode.displayName = 'BehaviorGraphNode'

const BehaviorNetworkNodeView = memo(({ data }: NodeProps<BehaviorNetworkNodeData>) => {
  const toneClassName: Record<BehaviorNetworkNodeData['tone'], string> = {
    amber: 'border-amber-500/40 bg-amber-500 text-white shadow-amber-500/15',
    primary: 'border-primary/40 bg-primary text-primary-foreground shadow-primary/15',
    slate: 'border-slate-400/45 bg-slate-600 text-white shadow-slate-500/10',
    teal: 'border-teal-500/40 bg-teal-600 text-white shadow-teal-500/15',
    violet: 'border-violet-500/40 bg-violet-600 text-white shadow-violet-500/15',
  }
  return (
    <div
      className={cn(
        'behavior-network-node-drag-handle group flex h-14 w-14 cursor-grab items-center justify-center rounded-full border text-center text-[10px] font-semibold shadow-lg transition active:cursor-grabbing hover:z-10 hover:scale-105 hover:ring-2 hover:ring-primary/30',
        toneClassName[data.tone]
      )}
      title={`${data.detail}\n${data.metric}`}
    >
      <Handle className="opacity-0" type="target" position={Position.Left} />
      <span className="line-clamp-2 px-1 opacity-0 transition group-hover:opacity-100">{data.label}</span>
      <Handle className="opacity-0" type="source" position={Position.Right} />
    </div>
  )
})

BehaviorNetworkNodeView.displayName = 'BehaviorNetworkNodeView'

const behaviorNodeTypes: NodeTypes = {
  behavior: BehaviorGraphNode,
  behaviorNetwork: BehaviorNetworkNodeView,
}

function formatTime(value: string | null): string {
  if (!value) return '-'
  return new Date(value).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function splitTags(value: string): string[] {
  return value
    .split(/[，,、\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatScore(value: number): string {
  return Number.isFinite(value) ? value.toFixed(2) : '0.00'
}

function shortText(value: string, maxLength = 72): string {
  if (value.length <= maxLength) return value
  return `${value.slice(0, maxLength)}...`
}

function formatProbability(value: number): string {
  if (!Number.isFinite(value)) return '0%'
  return `${Math.round(value * 100)}%`
}

function behaviorPathTypeLabel(path: Pick<BehaviorPathItem, 'actor_type' | 'learning_type'>): string {
  if (path.actor_type === 'maibot_self' && path.learning_type === 'self_reflection') return '自身反馈'
  if (path.actor_type === 'group_collective') return '群体观察'
  if (path.actor_type === 'other_user') return '他人观察'
  return '未知来源'
}

function isSelfReflectionPath(path: Pick<BehaviorPathItem, 'actor_type' | 'learning_type'>): boolean {
  return path.actor_type === 'maibot_self' && path.learning_type === 'self_reflection'
}

function topClusterTags(tags: BehaviorClusterTag[], maxCount = 6): BehaviorClusterTag[] {
  return tags
    .slice()
    .sort((left, right) => right.probability - left.probability)
    .slice(0, maxCount)
}

function tagDisplayText(tag: BehaviorClusterTag): string {
  const display = tag.display?.trim()
  if (display) return display
  return tag.tag
}

function isInternalTagRef(value: string): boolean {
  return /^(domain|need|attitude|scene):[a-z]+_[0-9a-f]{16,}$/i.test(value.trim())
}

function clusterTitle(name: string, tags: BehaviorClusterTag[]): string {
  const tagNames = topClusterTags(tags, 3)
    .map(tagDisplayText)
    .filter((value) => value && !isInternalTagRef(value))
  if (tagNames.length > 0) return tagNames.join(' · ')
  return name || '未命名场景簇'
}

function sceneGroupSortValue(group: BehaviorSceneGroup, sortBy: BehaviorPathSortBy): number | string {
  if (sortBy === 'activation_count') return group.activationCount
  if (sortBy === 'failure_count') return group.failureCount
  if (sortBy === 'scene_cluster_source_count') return group.clusterSourceCount
  if (sortBy === 'score') return group.bestScore
  if (sortBy === 'success_count') return group.successCount
  if (sortBy === 'count') return group.paths.reduce((sum, path) => sum + path.count, 0)
  if (sortBy === 'last_active_time') return group.paths.reduce((latest, path) => (
    path.last_active_time && path.last_active_time > latest ? path.last_active_time : latest
  ), '')
  if (sortBy === 'last_feedback_time') return group.paths.reduce((latest, path) => (
    path.last_feedback_time && path.last_feedback_time > latest ? path.last_feedback_time : latest
  ), '')
  return group.latestUpdate ?? ''
}

function compareSceneGroups(
  left: BehaviorSceneGroup,
  right: BehaviorSceneGroup,
  sortBy: BehaviorPathSortBy,
  sortOrder: SortOrder
): number {
  const leftValue = sceneGroupSortValue(left, sortBy)
  const rightValue = sceneGroupSortValue(right, sortBy)
  const direction = sortOrder === 'asc' ? 1 : -1
  if (typeof leftValue === 'number' && typeof rightValue === 'number') {
    return (leftValue - rightValue) * direction
  }
  return String(leftValue).localeCompare(String(rightValue)) * direction
}

function clusterCountBucket(pathCount: number): string {
  if (pathCount <= 0) return '未连接行为路径'
  if (pathCount === 1) return '1 条路径'
  if (pathCount <= 4) return '2-4 条路径'
  if (pathCount <= 9) return '5-9 条路径'
  return '10 条以上路径'
}

function clusterGroupLabel(cluster: BehaviorClusterItem, groupMode: BehaviorClusterGroupMode): string {
  if (groupMode === 'count') return clusterCountBucket(cluster.path_count)
  return '全部场景簇'
}

function groupedClusters(
  clusters: BehaviorClusterItem[],
  groupMode: BehaviorClusterGroupMode
): Array<{ key: string; label: string; clusters: BehaviorClusterItem[] }> {
  const groups = new Map<string, { key: string; label: string; clusters: BehaviorClusterItem[] }>()
  clusters.forEach((cluster) => {
    const label = clusterGroupLabel(cluster, groupMode)
    const key = groupMode === 'count'
        ? `count:${clusterCountBucket(cluster.path_count)}`
        : 'all'
    const group = groups.get(key) ?? { key, label, clusters: [] }
    group.clusters.push(cluster)
    groups.set(key, group)
  })
  return Array.from(groups.values()).sort((left, right) => {
    const order = ['未连接行为路径', '1 条路径', '2-4 条路径', '5-9 条路径', '10 条以上路径']
    return order.indexOf(left.label) - order.indexOf(right.label)
  })
}

function stableHash(value: string): number {
  return Array.from(value).reduce((hash, char) => (hash * 33 + char.charCodeAt(0)) % 1000003, 5381)
}

function clampNumber(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max)
}

function networkNodeTone(kind: string): BehaviorNetworkNodeData['tone'] {
  if (kind === 'need') return 'amber'
  if (kind === 'attitude') return 'teal'
  if (kind === 'domain') return 'primary'
  if (kind === 'scene') return 'violet'
  return 'slate'
}

function networkCirclePosition(index: number, count: number, weight: number): { x: number; y: number } {
  const ring = Math.floor(Math.sqrt(index))
  const angle = (index / Math.max(count, 1)) * Math.PI * 2 + ring * 0.48
  const radius = 130 + ring * 64 + Math.max(0, 90 - weight * 8)
  return {
    x: 920 + Math.cos(angle) * radius,
    y: 500 + Math.sin(angle) * radius,
  }
}

function buildSceneNetworkGraph(data: BehaviorGraphData | null): {
  nodes: BehaviorNetworkNode[]
  edges: BehaviorNetworkEdge[]
  nodeById: Map<string, BehaviorGraphData['scene_cluster_network']['nodes'][number]>
} {
  const sourceNodes = data?.scene_cluster_network?.nodes ?? []
  const sourceEdges = data?.scene_cluster_network?.edges ?? []
  const sortedNodes = sourceNodes.slice().sort((left, right) => right.path_count - left.path_count || right.source_count - left.source_count)
  const nodeById = new Map<string, BehaviorGraphData['scene_cluster_network']['nodes'][number]>()
  const nodes: BehaviorNetworkNode[] = sortedNodes.map((node, index) => {
    const nodeId = `scene:${node.id}`
    const position = networkCirclePosition(index, sortedNodes.length, node.path_count + node.source_count * 0.2)
    nodeById.set(nodeId, node)
    return {
      id: nodeId,
      type: 'behaviorNetwork',
      draggable: true,
      dragHandle: '.behavior-network-node-drag-handle',
      selectable: true,
      position,
      data: {
        label: shortText(node.short_label || node.label, 16),
        detail: node.label,
        metric: `${node.path_count} 路径 · ${node.source_count} 样本`,
        tone: 'violet',
      },
    }
  })
  const visibleNodeIds = new Set(nodes.map((node) => node.id))
  const edges: BehaviorNetworkEdge[] = sourceEdges
    .filter((edge) => visibleNodeIds.has(`scene:${edge.source}`) && visibleNodeIds.has(`scene:${edge.target}`))
    .slice(0, 900)
    .map((edge) => ({
      id: `scene-edge:${edge.source}:${edge.target}`,
      source: `scene:${edge.source}`,
      target: `scene:${edge.target}`,
      type: 'straight',
      interactionWidth: 16,
      style: {
        stroke: 'hsl(var(--muted-foreground))',
        strokeWidth: Math.max(0.8, Math.min(5, edge.weight * 5)),
        opacity: Math.max(0.12, Math.min(0.5, edge.weight)),
      },
      data: edge,
    }))
  return { nodes, edges, nodeById }
}

function buildTagNetworkGraph(data: BehaviorGraphData | null): {
  nodes: BehaviorNetworkNode[]
  edges: BehaviorNetworkEdge[]
  nodeById: Map<string, BehaviorGraphData['tag_network']['nodes'][number]>
} {
  const sourceNodes = data?.tag_network?.nodes ?? []
  const sourceEdges = data?.tag_network?.edges ?? []
  const connectedIds = new Set(sourceEdges.flatMap((edge) => [edge.source, edge.target]))
  const sortedNodes = sourceNodes
    .filter((node) => connectedIds.has(node.id))
    .sort((left, right) => right.scene_count - left.scene_count || right.source_count - left.source_count)
    .slice(0, 360)
  const nodeById = new Map<string, BehaviorGraphData['tag_network']['nodes'][number]>()
  const nodes: BehaviorNetworkNode[] = sortedNodes.map((node, index) => {
    const nodeId = `tag:${node.id}`
    const position = networkCirclePosition(index, sortedNodes.length, node.scene_count + node.source_count * 0.05)
    nodeById.set(nodeId, node)
    return {
      id: nodeId,
      type: 'behaviorNetwork',
      draggable: true,
      dragHandle: '.behavior-network-node-drag-handle',
      selectable: true,
      position,
      data: {
        label: shortText(node.label, 16),
        detail: `${node.label}\n${node.id}`,
        metric: `${node.scene_count} 场景簇 · ${node.source_count} 成员样本`,
        tone: networkNodeTone(node.kind),
      },
    }
  })
  const visibleNodeIds = new Set(nodes.map((node) => node.id))
  const edges: BehaviorNetworkEdge[] = sourceEdges
    .filter((edge) => visibleNodeIds.has(`tag:${edge.source}`) && visibleNodeIds.has(`tag:${edge.target}`))
    .slice(0, 1200)
    .map((edge) => ({
      id: `tag-edge:${edge.source}:${edge.target}`,
      source: `tag:${edge.source}`,
      target: `tag:${edge.target}`,
      type: 'straight',
      interactionWidth: 16,
      style: {
        stroke: 'hsl(var(--muted-foreground))',
        strokeWidth: Math.max(0.7, Math.min(5, Math.sqrt(edge.weight) * 1.5)),
        opacity: Math.max(0.1, Math.min(0.42, edge.weight * 0.8)),
      },
      data: edge,
    }))
  return { nodes, edges, nodeById }
}

function edgeWeight(edge: BehaviorNetworkEdge): number {
  const data = edge.data
  if (data && typeof data === 'object' && 'weight' in data) {
    const weight = Number(data.weight)
    return Number.isFinite(weight) && weight > 0 ? weight : 1
  }
  return 1
}

export function BehaviorLearningPage() {
  const [activeTab, setActiveTab] = useState<ActiveTab>('paths')
  const [selectedSessionId, setSelectedSessionId] = useState('all')
  // searchInput 为输入框内的草稿值，search 为已提交（点搜索/回车）的查询参数
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [enabledFilter, setEnabledFilter] = useState('all')
  const [learningTypeFilter, setLearningTypeFilter] = useState('all')
  const [sortBy, setSortBy] = useState<BehaviorPathSortBy>('update_time')
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc')
  // 场景簇浏览（同事新增功能）：列表数据与控制态保留为本地内联管理
  const [clusterSearch, setClusterSearch] = useState('')
  const [clusterSortBy, setClusterSortBy] = useState<BehaviorClusterSortBy>('update_time')
  const [clusterSortOrder, setClusterSortOrder] = useState<SortOrder>('desc')
  const [clusterGroupMode, setClusterGroupMode] = useState<BehaviorClusterGroupMode>('none')
  const [clusterPage, setClusterPage] = useState(1)
  const [clusters, setClusters] = useState<BehaviorClusterItem[]>([])
  const [clusterTotal, setClusterTotal] = useState(0)
  const [clusterLoading, setClusterLoading] = useState(false)
  const [openSceneGroups, setOpenSceneGroups] = useState<Set<string>>(new Set())
  const [page, setPage] = useState(1)
  const [selectedPathId, setSelectedPathId] = useState<number | null>(null)
  const [debugForm, setDebugForm] = useState({
    sceneText: '',
    domainTags: '',
    behaviorNeeds: '',
    otherTraits: '',
  })

  // 聊天流 / 路径 / 图谱 / 详情均为只读服务端态，下沉到领域 hook（边下沉边转 Query）
  const { toast } = useToast()
  const { chats, refetch: refetchChats } = useBehaviorChats()
  const { paths, total, loading, refetch: refetchPaths } = useBehaviorPaths({
    sessionId: selectedSessionId,
    search,
    enabledFilter,
    learningTypeFilter,
    sortBy,
    sortOrder,
    page,
  })
  const isNetworkTab = activeTab === 'scene-network' || activeTab === 'tag-network'
  const { graphData, loading: graphLoading } = useBehaviorGraph({
    sessionId: selectedSessionId,
    enabled: isNetworkTab,
  })
  const { detail, loading: detailLoading } = useBehaviorPathDetail(selectedPathId)
  const { runDebug, result: debugResult, loading: debugLoading } = useBehaviorDebug()

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const clusterTotalPages = Math.max(1, Math.ceil(clusterTotal / PAGE_SIZE))
  const selectedChatName = useMemo(() => {
    if (selectedSessionId === 'all') return '全部聊天流'
    if (selectedSessionId === '__global__') return '全局行为'
    return chats.find((chat) => chat.session_id === selectedSessionId)?.display_name ?? selectedSessionId
  }, [chats, selectedSessionId])
  const sceneGroups = useMemo(() => {
    const groups = new Map<string, BehaviorSceneGroup>()
    paths.forEach((path) => {
      const clusterKey = path.scene_cluster_id ?? path.scene_cluster_name
      const key = `${path.session_id || '__global__'}::cluster:${clusterKey}`
      const existing = groups.get(key)
      if (!existing) {
        groups.set(key, {
          key,
          sceneClusterId: path.scene_cluster_id,
          clusterName: path.scene_cluster_name,
          clusterTags: path.scene_cluster_tags,
          clusterSourceCount: path.scene_cluster_source_count,
          chatName: path.chat_name,
          paths: [path],
          latestUpdate: path.update_time,
          bestScore: path.score,
          activationCount: path.activation_count,
          successCount: path.success_count,
          failureCount: path.failure_count,
        })
        return
      }
      existing.paths.push(path)
      existing.bestScore = Math.max(existing.bestScore, path.score)
      existing.activationCount += path.activation_count
      existing.successCount += path.success_count
      existing.failureCount += path.failure_count
      if (!existing.latestUpdate || (path.update_time && path.update_time > existing.latestUpdate)) {
        existing.latestUpdate = path.update_time
      }
    })
    const sortedGroups = Array.from(groups.values())
    sortedGroups.sort((left, right) => compareSceneGroups(left, right, sortBy, sortOrder))
    return sortedGroups
  }, [paths, sortBy, sortOrder])
  const clusterGroups = useMemo(() => groupedClusters(clusters, clusterGroupMode), [clusterGroupMode, clusters])

  // 默认选中首条路径：保留原 loadPaths 内「无选中且有数据则选第一条」的行为。
  // 用「渲染期版本标记」模式（React 官方推荐）替代 effect 内 setState，避免级联渲染告警。
  if (selectedPathId === null && paths.length > 0) {
    setSelectedPathId(paths[0].id)
  }

  // 检索调试：组装请求并触发 mutation（写失败由 query.ts 弹全局 toast）
  const handleRunDebug = () => {
    runDebug({
      session_id: selectedSessionId === 'all' || selectedSessionId === '__global__' ? undefined : selectedSessionId,
      include_global: selectedSessionId === 'all',
      retrieval_mode: 'tag_cluster_spread_1',
      scene_text: debugForm.sceneText,
      tag_clusters: splitTags(debugForm.domainTags).map((tag) => ({ tag_name: tag, tag_aliases: [] })),
      need: { tag_name: splitTags(debugForm.behaviorNeeds)[0] ?? '', tag_aliases: [] },
      other_traits: splitTags(debugForm.otherTraits).map((tag) => ({ tag_name: tag, tag_aliases: [] })),
      max_count: 20,
    })
  }

  // 场景簇浏览（同事新增功能）：列表加载保留内联实现
  // 路径/图谱/详情/调试已由各自 useQuery hook 管理，不再内联
  const loadClusters = async (targetPage = clusterPage) => {
    try {
      setClusterLoading(true)
      const result = await listBehaviorClusters({
        session_id: selectedSessionId,
        search: clusterSearch,
        sort_by: clusterSortBy,
        sort_order: clusterSortOrder,
        page: targetPage,
        page_size: PAGE_SIZE,
      })
      const clusterData = result.data
      setClusters(clusterData)
      setClusterTotal(result.total ?? clusterData.length)
    } catch (error) {
      toast({
        title: '加载场景簇失败',
        description: error instanceof Error ? error.message : '无法读取行为场景簇',
        variant: 'destructive',
      })
    } finally {
      setClusterLoading(false)
    }
  }

  // 场景簇浏览 tab 激活时加载（搜索经 applyClusterSearch 手动提交，故不入依赖）
  useEffect(() => {
    if (activeTab === 'scene-browser') {
      loadClusters()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, selectedSessionId, clusterSortBy, clusterSortOrder, clusterPage])

  // 手动刷新：重拉聊天流/路径/场景簇（图谱由其 query 在网络 tab 激活时管理）
  const handleRefresh = () => {
    refetchChats()
    refetchPaths()
    loadClusters()
  }

  // 搜索：提交草稿值并重置分页（page=1），保留原 applySearch 的重置行为
  const applySearch = () => {
    setSearch(searchInput)
    setPage(1)
  }
  const applyClusterSearch = () => {
    setClusterPage(1)
    loadClusters(1)
  }
  const handleSessionChange = (value: string) => {
    setSelectedSessionId(value)
    setPage(1)
    setClusterPage(1)
  }
  const toggleSceneGroup = (groupKey: string) => {
    setOpenSceneGroups((current) => {
      const next = new Set(current)
      if (next.has(groupKey)) {
        next.delete(groupKey)
      } else {
        next.add(groupKey)
      }
      return next
    })
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-7xl flex-col gap-4 p-4 sm:p-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal">行为学习</h1>
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Select
            value={selectedSessionId}
            onValueChange={handleSessionChange}
          >
            <SelectTrigger className="w-full sm:w-64">
              <SelectValue placeholder="选择聊天流" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部聊天流</SelectItem>
              {chats.map((chat) => (
                <SelectItem key={chat.session_id || '__global__'} value={chat.session_id || '__global__'}>
                  {chat.display_name} · {chat.cluster_count} 簇
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={handleRefresh}>

            <RefreshCw className="mr-2 h-4 w-4" />
            刷新
          </Button>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as ActiveTab)} className="min-h-0 flex-1">
        <DashboardTabBar variant="grid" className="max-w-6xl grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
          <DashboardTabTrigger value="paths">经验路径</DashboardTabTrigger>
          <DashboardTabTrigger value="scene-browser">场景簇浏览</DashboardTabTrigger>
          <DashboardTabTrigger value="scene-network">场景簇图谱</DashboardTabTrigger>
          <DashboardTabTrigger value="tag-network">Tag簇网络</DashboardTabTrigger>
          <DashboardTabTrigger value="debug">检索调试</DashboardTabTrigger>
          <DashboardTabTrigger value="graph">局部图谱</DashboardTabTrigger>
        </DashboardTabBar>

        <TabsContent value="paths" className="mt-4 min-h-0 space-y-4">
          <div className="grid min-w-0 gap-2 rounded-lg border bg-background p-3 md:grid-cols-[minmax(9rem,1fr)_repeat(4,minmax(6.75rem,8rem))_auto] md:items-center">
            <div className="relative min-w-0">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') applySearch() }}
                placeholder="搜索场景簇 tag、行为、结果"
                className="min-w-0 pl-9"
              />
            </div>
            <Select value={enabledFilter} onValueChange={(value) => { setEnabledFilter(value); setPage(1) }}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="true">启用中</SelectItem>
                <SelectItem value="false">已停用</SelectItem>
              </SelectContent>
            </Select>
            <Select value={learningTypeFilter} onValueChange={(value) => { setLearningTypeFilter(value); setPage(1) }}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                <SelectItem value="observed_behavior">观察学习</SelectItem>
                <SelectItem value="self_reflection">自身反馈</SelectItem>
              </SelectContent>
            </Select>
            <Select value={sortBy} onValueChange={(value) => { setSortBy(value as BehaviorPathSortBy); setPage(1) }}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.entries(PATH_SORT_LABELS) as Array<[BehaviorPathSortBy, string]>).map(([value, label]) => (
                  <SelectItem key={value} value={value}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={sortOrder} onValueChange={(value) => { setSortOrder(value as SortOrder); setPage(1) }}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="desc">降序</SelectItem>
                <SelectItem value="asc">升序</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={applySearch}>搜索</Button>
          </div>

          <div className="overflow-hidden rounded-lg border bg-background">
            <div className="flex items-center justify-between border-b px-4 py-3 text-sm text-muted-foreground">
              <span>{selectedChatName} · {sceneGroups.length} 个场景簇 · {total} 条经验路径</span>
              {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            </div>
            <ScrollArea className="h-[560px]">
              <div className="divide-y">
                {paths.length === 0 && !loading ? (
                  <div className="p-8 text-center text-sm text-muted-foreground">暂无行为经验路径</div>
                ) : (
                  sceneGroups.map((group) => (
                    <SceneGroupRow
                      key={group.key}
                      group={group}
                      open={openSceneGroups.has(group.key)}
                      selectedPathId={selectedPathId}
                      onToggle={() => toggleSceneGroup(group.key)}
                      onSelectPath={(pathId) => {
                        setSelectedPathId(pathId)
                        setActiveTab('graph')
                      }}
                    />
                  ))
                )}
              </div>
            </ScrollArea>
            <div className="flex items-center justify-between border-t px-4 py-3">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                <ChevronLeft className="mr-1 h-4 w-4" />
                上一页
              </Button>
              <span className="text-sm text-muted-foreground">{page} / {totalPages}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((value) => value + 1)}
              >
                下一页
                <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="scene-browser" className="mt-4 min-h-0 space-y-4">
          <div className="grid min-w-0 gap-2 rounded-lg border bg-background p-3 md:grid-cols-[minmax(9rem,1fr)_minmax(11rem,16rem)_repeat(3,minmax(6.75rem,8.5rem))_auto] md:items-center">
            <div className="relative min-w-0">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={clusterSearch}
                onChange={(event) => setClusterSearch(event.target.value)}
                onKeyDown={(event) => { if (event.key === 'Enter') applyClusterSearch() }}
                placeholder="搜索场景簇 tag"
                className="min-w-0 pl-9"
              />
            </div>
            <Select value={selectedSessionId} onValueChange={handleSessionChange}>
              <SelectTrigger className="min-w-0">
                <SelectValue placeholder="选择聊天流" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部聊天流</SelectItem>
                {chats.map((chat) => (
                  <SelectItem key={chat.session_id || '__global__'} value={chat.session_id || '__global__'}>
                    {chat.display_name} · {chat.cluster_count} 簇
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={clusterGroupMode} onValueChange={(value) => setClusterGroupMode(value as BehaviorClusterGroupMode)}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">不分层</SelectItem>
                <SelectItem value="count">按路径数量分层</SelectItem>
              </SelectContent>
            </Select>
            <Select value={clusterSortBy} onValueChange={(value) => { setClusterSortBy(value as BehaviorClusterSortBy); setClusterPage(1) }}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(Object.entries(CLUSTER_SORT_LABELS) as Array<[BehaviorClusterSortBy, string]>).map(([value, label]) => (
                  <SelectItem key={value} value={value}>{label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={clusterSortOrder} onValueChange={(value) => { setClusterSortOrder(value as SortOrder); setClusterPage(1) }}>
              <SelectTrigger className="min-w-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="desc">降序</SelectItem>
                <SelectItem value="asc">升序</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={applyClusterSearch}>搜索</Button>
          </div>

          <div className="overflow-hidden rounded-lg border bg-background">
            <div className="flex items-center justify-between border-b px-4 py-3 text-sm text-muted-foreground">
              <span>{selectedChatName} · {clusterTotal} 个场景簇</span>
              {clusterLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            </div>
            <ScrollArea className="h-[620px]">
              {clusters.length === 0 && !clusterLoading ? (
                <div className="p-8 text-center text-sm text-muted-foreground">暂无场景簇</div>
              ) : (
                <div className="divide-y">
                  {clusterGroups.map((group) => (
                    <div key={group.key} className="space-y-2 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold">{group.label}</div>
                        {clusterGroupMode !== 'none' && <Badge variant="outline">{group.clusters.length} 个场景簇</Badge>}
                      </div>
                      <div className="grid gap-3 lg:grid-cols-2">
                        {group.clusters.map((cluster) => (
                          <SceneClusterCard key={cluster.id ?? `${cluster.session_id}:${cluster.name}`} cluster={cluster} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </ScrollArea>
            <div className="flex items-center justify-between border-t px-4 py-3">
              <Button
                variant="outline"
                size="sm"
                disabled={clusterPage <= 1}
                onClick={() => setClusterPage((value) => value - 1)}
              >
                <ChevronLeft className="mr-1 h-4 w-4" />
                上一页
              </Button>
              <span className="text-sm text-muted-foreground">{clusterPage} / {clusterTotalPages}</span>
              <Button
                variant="outline"
                size="sm"
                disabled={clusterPage >= clusterTotalPages}
                onClick={() => setClusterPage((value) => value + 1)}
              >
                下一页
                <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="scene-network" className="mt-4">
          <BehaviorSceneNetworkView
            graphData={graphData}
            loading={graphLoading}
            selectedChatName={selectedChatName}
          />
        </TabsContent>

        <TabsContent value="tag-network" className="mt-4">
          <BehaviorTagNetworkView
            graphData={graphData}
            loading={graphLoading}
            selectedChatName={selectedChatName}
          />
        </TabsContent>

        <TabsContent value="debug" className="mt-4 grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
          <div className="space-y-3 rounded-lg border bg-background p-4">
            <h2 className="text-base font-semibold">输入场景画像</h2>
            <Field label="用一句话描述聊天场景">
              <Textarea
                value={debugForm.sceneText}
                onChange={(event) => setDebugForm({ ...debugForm, sceneText: event.target.value })}
                placeholder="例如：群里有人焦虑地追问插件启动失败，其他人正在帮他排查配置。"
                className="min-h-20"
              />
            </Field>
            <Field label="领域标签">
              <Input value={debugForm.domainTags} onChange={(event) => setDebugForm({ ...debugForm, domainTags: event.target.value })} placeholder="用逗号分隔" />
            </Field>
            <Field label="行为需求">
              <Input value={debugForm.behaviorNeeds} onChange={(event) => setDebugForm({ ...debugForm, behaviorNeeds: event.target.value })} placeholder="用逗号分隔" />
            </Field>
            <Field label="他人特点/态度">
              <Input value={debugForm.otherTraits} onChange={(event) => setDebugForm({ ...debugForm, otherTraits: event.target.value })} placeholder="用逗号分隔" />
            </Field>
            <Button className="w-full" onClick={handleRunDebug} disabled={debugLoading}>
              {debugLoading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <GitBranch className="mr-2 h-4 w-4" />}
              试跑检索
            </Button>
          </div>
          <RetrievalDebugView result={debugResult} />
        </TabsContent>

        <TabsContent value="graph" className="mt-4">
          <PathGraphView detail={detail} loading={detailLoading} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function BehaviorSceneNetworkView({
  graphData,
  loading,
  selectedChatName,
}: {
  graphData: BehaviorGraphData | null
  loading: boolean
  selectedChatName: string
}) {
  const { nodes, edges, nodeById } = useMemo(() => buildSceneNetworkGraph(graphData), [graphData])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) ?? null : null
  return (
    <NetworkShell
      title="场景簇图谱"
      description={`${selectedChatName} · 节点表示场景簇，连线表示 tag 概率分布重叠`}
      loading={loading}
      empty={!loading && nodes.length === 0}
      nodes={nodes}
      edges={edges}
      networkKind="scene"
      onNodeSelect={setSelectedNodeId}
      selectedNodeId={selectedNodeId}
      detail={selectedNode ? <SceneNetworkDetail node={selectedNode} /> : null}
    />
  )
}

function BehaviorTagNetworkView({
  graphData,
  loading,
  selectedChatName,
}: {
  graphData: BehaviorGraphData | null
  loading: boolean
  selectedChatName: string
}) {
  const { nodes, edges, nodeById } = useMemo(() => buildTagNetworkGraph(graphData), [graphData])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const selectedNode = selectedNodeId ? nodeById.get(selectedNodeId) ?? null : null
  return (
    <NetworkShell
      title="Tag簇分布网络"
      description={`${selectedChatName} · 节点表示 tag 簇，连线表示共同出现在同一场景簇分布`}
      loading={loading}
      empty={!loading && nodes.length === 0}
      nodes={nodes}
      edges={edges}
      networkKind="tag"
      onNodeSelect={setSelectedNodeId}
      selectedNodeId={selectedNodeId}
      detail={selectedNode ? <TagNetworkDetail node={selectedNode} /> : null}
    />
  )
}

function canvasToneColor(tone: BehaviorNetworkNodeData['tone']): string {
  if (tone === 'amber') return '#f59e0b'
  if (tone === 'teal') return '#0d9488'
  if (tone === 'primary') return '#2563eb'
  if (tone === 'violet') return '#7c3aed'
  return '#475569'
}

function canvasNodeWeight(node: BehaviorNetworkNode): number {
  const numbers = node.data.metric.match(/\d+/g)?.map(Number).filter(Number.isFinite) ?? []
  return Math.max(1, ...numbers)
}

function buildCanvasNetworkNode(
  node: BehaviorNetworkNode,
  index: number,
  count: number,
  networkKind: CanvasNetworkKind
): CanvasNetworkNode {
  const angle = networkKind === 'scene'
    ? index / Math.max(count, 1) * Math.PI * 2
    : index * 2.399963229728653
  const layoutRadius = networkKind === 'scene'
    ? 420 + ((stableHash(`${node.id}:ring`) % 1000) / 1000 - 0.5) * 80
    : 360 + Math.floor(Math.sqrt(index)) * 52
  const weight = canvasNodeWeight(node)
  const nodeRadius = networkKind === 'scene'
    ? clampNumber(6 + Math.sqrt(weight) * 1.2, 6, 26)
    : clampNumber(5 + Math.sqrt(weight) * 0.45, 5, 24)
  return {
    id: node.id,
    x: Math.cos(angle) * layoutRadius + ((stableHash(node.id) % 1000) / 1000 - 0.5) * 60,
    y: Math.sin(angle) * layoutRadius + ((stableHash(`${node.id}:y`) % 1000) / 1000 - 0.5) * 60,
    vx: 0,
    vy: 0,
    fixed: false,
    radius: nodeRadius,
    label: node.data.label,
    detail: node.data.detail,
    metric: node.data.metric,
    tone: node.data.tone,
  }
}

function fitCanvasNetwork(nodes: CanvasNetworkNode[], width: number, height: number): CanvasTransform {
  if (nodes.length === 0 || width <= 0 || height <= 0) return { x: 0, y: 0, zoom: 1 }
  const bounds = nodes.reduce(
    (result, node) => ({
      minX: Math.min(result.minX, node.x),
      minY: Math.min(result.minY, node.y),
      maxX: Math.max(result.maxX, node.x),
      maxY: Math.max(result.maxY, node.y),
    }),
    { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity }
  )
  const graphWidth = Math.max(bounds.maxX - bounds.minX, 1)
  const graphHeight = Math.max(bounds.maxY - bounds.minY, 1)
  const zoom = clampNumber(Math.min(width / (graphWidth + 180), height / (graphHeight + 180)), 0.08, 2.2)
  return {
    x: width / 2 - ((bounds.minX + bounds.maxX) / 2) * zoom,
    y: height / 2 - ((bounds.minY + bounds.maxY) / 2) * zoom,
    zoom,
  }
}

function BehaviorNetworkCanvas({
  nodes,
  edges,
  networkKind,
  settings,
  layoutVersion,
  selectedNodeId,
  onNodeSelect,
}: {
  nodes: BehaviorNetworkNode[]
  edges: BehaviorNetworkEdge[]
  networkKind: CanvasNetworkKind
  settings: CanvasNetworkSettings
  layoutVersion: number
  selectedNodeId: string | null
  onNodeSelect: (nodeId: string | null) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const nodesRef = useRef<CanvasNetworkNode[]>([])
  const edgeRef = useRef<Array<{ source: string; target: string; weight: number }>>([])
  const transformRef = useRef<CanvasTransform>({ x: 0, y: 0, zoom: 1 })
  const selectedNodeIdRef = useRef<string | null>(selectedNodeId)
  const settingsRef = useRef(settings)
  const maxWeightRef = useRef(1)
  const heatRef = useRef(0.9)
  const pointerRef = useRef<{
    mode: 'node' | 'pan' | null
    nodeId: string | null
    lastX: number
    lastY: number
  }>({ mode: null, nodeId: null, lastX: 0, lastY: 0 })
  const layoutVersionRef = useRef(layoutVersion)

  useEffect(() => {
    selectedNodeIdRef.current = selectedNodeId
  }, [selectedNodeId])

  useEffect(() => {
    settingsRef.current = settings
    heatRef.current = Math.max(heatRef.current, 0.55)
  }, [settings])

  useEffect(() => {
    const shouldResetLayout = layoutVersionRef.current !== layoutVersion
    layoutVersionRef.current = layoutVersion
    const previous = shouldResetLayout ? new Map<string, CanvasNetworkNode>() : new Map(nodesRef.current.map((node) => [node.id, node]))
    nodesRef.current = nodes.map((node, index) => {
      const existing = previous.get(node.id)
      if (!existing) return buildCanvasNetworkNode(node, index, nodes.length, networkKind)
      const nextNode = buildCanvasNetworkNode(node, index, nodes.length, networkKind)
      return {
        ...nextNode,
        x: existing.x,
        y: existing.y,
        vx: existing.vx,
        vy: existing.vy,
        fixed: existing.fixed,
        radius: nextNode.radius,
      }
    })
    edgeRef.current = edges.map((edge) => ({
      source: edge.source,
      target: edge.target,
      weight: edgeWeight(edge),
    }))
    maxWeightRef.current = Math.max(1, ...edgeRef.current.map((edge) => edge.weight))
    const canvas = canvasRef.current
    if (canvas) {
      transformRef.current = fitCanvasNetwork(nodesRef.current, canvas.clientWidth, canvas.clientHeight)
    }
    heatRef.current = 0.9
  }, [edges, layoutVersion, networkKind, nodes, settings])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const resize = () => {
      const width = canvas.clientWidth
      const height = canvas.clientHeight
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.floor(width * ratio))
      canvas.height = Math.max(1, Math.floor(height * ratio))
      transformRef.current = fitCanvasNetwork(nodesRef.current, width, height)
      heatRef.current = Math.max(heatRef.current, 0.4)
    }
    resize()
    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const handleWheel = (event: globalThis.WheelEvent) => {
      event.preventDefault()
      event.stopPropagation()
      const rect = canvas.getBoundingClientRect()
      const transform = transformRef.current
      const oldZoom = transform.zoom
      const nextZoom = clampNumber(oldZoom * (event.deltaY < 0 ? 1.1 : 0.9), 0.15, 4)
      const mouseX = event.clientX - rect.left
      const mouseY = event.clientY - rect.top
      const worldX = (mouseX - transform.x) / oldZoom
      const worldY = (mouseY - transform.y) / oldZoom
      transform.zoom = nextZoom
      transform.x = mouseX - worldX * nextZoom
      transform.y = mouseY - worldY * nextZoom
    }

    canvas.addEventListener('wheel', handleWheel, { passive: false })
    return () => canvas.removeEventListener('wheel', handleWheel)
  }, [networkKind])

  const screenToWorld = useCallback((clientX: number, clientY: number): { x: number; y: number } => {
    const canvas = canvasRef.current
    const rect = canvas?.getBoundingClientRect()
    const transform = transformRef.current
    if (!rect) return { x: 0, y: 0 }
    return {
      x: (clientX - rect.left - transform.x) / transform.zoom,
      y: (clientY - rect.top - transform.y) / transform.zoom,
    }
  }, [])

  const findNodeAt = useCallback((clientX: number, clientY: number): CanvasNetworkNode | null => {
    const world = screenToWorld(clientX, clientY)
    const fallbackRadius = 28
    let bestNode: CanvasNetworkNode | null = null
    let bestDistance = Infinity
    nodesRef.current.forEach((node) => {
      const radius = Math.max(node.radius, fallbackRadius)
      const distance = Math.hypot(world.x - node.x, world.y - node.y)
      if (distance <= radius && distance < bestDistance) {
        bestNode = node
        bestDistance = distance
      }
    })
    return bestNode
  }, [screenToWorld])

  useEffect(() => {
    const canvas = canvasRef.current
    const context = canvas?.getContext('2d')
    if (!canvas || !context) return

    let frameId = 0
    let disposed = false

    const step = () => {
      const graphNodes = nodesRef.current
      const nodeById = new Map(graphNodes.map((node) => [node.id, node]))
      const heat = heatRef.current
      const settings = settingsRef.current
      const charge = networkKind === 'scene' ? 1300 : 2200
      const linkStrength = networkKind === 'scene' ? 0.005 : 0.003

      if (!settings.paused) {
        graphNodes.forEach((node) => {
          if (node.fixed) return
          node.vx = node.vx * 0.82 + -node.x * 0.0008 * heat
          node.vy = node.vy * 0.82 + -node.y * 0.0008 * heat
        })

        if (graphNodes.length <= 520) {
          for (let leftIndex = 0; leftIndex < graphNodes.length; leftIndex += 1) {
            for (let rightIndex = leftIndex + 1; rightIndex < graphNodes.length; rightIndex += 1) {
              const left = graphNodes[leftIndex]
              const right = graphNodes[rightIndex]
              const dx = right.x - left.x
              const dy = right.y - left.y
              const distanceSquared = Math.max(dx * dx + dy * dy, 25)
              const distance = Math.sqrt(distanceSquared)
              const force = charge / distanceSquared * heat
              const fx = dx / distance * force
              const fy = dy / distance * force
              if (!left.fixed) {
                left.vx -= fx
                left.vy -= fy
              }
              if (!right.fixed) {
                right.vx += fx
                right.vy += fy
              }
            }
          }
        }

        edgeRef.current.forEach((edge) => {
          const source = nodeById.get(edge.source)
          const target = nodeById.get(edge.target)
          if (!source || !target) return
          const dx = target.x - source.x
          const dy = target.y - source.y
          const distance = Math.max(Math.hypot(dx, dy), 1)
          const targetDistance = networkKind === 'scene'
            ? 80 + 80 / (1 + edge.weight)
            : 140 + 115 / Math.max(1, Math.sqrt(edge.weight))
          const force = (distance - targetDistance) * linkStrength * heat
          const fx = dx / distance * force
          const fy = dy / distance * force
          if (!source.fixed) {
            source.vx += fx
            source.vy += fy
          }
          if (!target.fixed) {
            target.vx -= fx
            target.vy -= fy
          }
        })

        graphNodes.forEach((node) => {
          if (node.fixed) return
          node.x += node.vx
          node.y += node.vy
        })
        heatRef.current = Math.max(0.08, heat * 0.992)
      }

      const ratio = window.devicePixelRatio || 1
      const width = canvas.clientWidth
      const height = canvas.clientHeight
      const transform = transformRef.current
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      context.clearRect(0, 0, width, height)
      context.save()
      context.translate(transform.x, transform.y)
      context.scale(transform.zoom, transform.zoom)

      context.lineCap = 'round'
      const maxWeight = maxWeightRef.current
      edgeRef.current.forEach((edge) => {
        const source = nodeById.get(edge.source)
        const target = nodeById.get(edge.target)
        if (!source || !target) return
        context.beginPath()
        context.moveTo(source.x, source.y)
        context.lineTo(target.x, target.y)
        context.strokeStyle = `rgba(82, 95, 117, ${clampNumber(0.12 + edge.weight / maxWeight * 0.6, 0.12, 0.72)})`
        context.lineWidth = clampNumber(Math.sqrt(edge.weight) * 4, 0.7, 8)
        context.stroke()
      })

      graphNodes.forEach((node) => {
        const selected = selectedNodeIdRef.current === node.id
        const radius = node.radius
        context.beginPath()
        context.arc(node.x, node.y, radius, 0, Math.PI * 2)
        context.fillStyle = canvasToneColor(node.tone)
        context.fill()
        context.lineWidth = selected ? 4 : 1.6
        context.strokeStyle = selected ? '#111827' : '#ffffff'
        context.stroke()
        if (settings.showLabels && (selected || graphNodes.length < 150 || radius > 12)) {
          context.fillStyle = '#1d2430'
          context.font = '12px Microsoft YaHei, Segoe UI, sans-serif'
          context.textAlign = 'center'
          context.textBaseline = 'top'
          const label = node.label.length > 22 ? `${node.label.slice(0, 22)}...` : node.label
          context.fillText(label, node.x, node.y + radius + 4, 160)
        }
      })

      context.restore()
      if (!disposed) {
        frameId = window.requestAnimationFrame(step)
      }
    }

    frameId = window.requestAnimationFrame(step)
    return () => {
      disposed = true
      window.cancelAnimationFrame(frameId)
    }
  }, [networkKind])

  const handlePointerDown = useCallback((event: PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas) return
    canvas.setPointerCapture(event.pointerId)
    const node = findNodeAt(event.clientX, event.clientY)
    pointerRef.current = {
      mode: node ? 'node' : 'pan',
      nodeId: node?.id ?? null,
      lastX: event.clientX,
      lastY: event.clientY,
    }
    if (node) {
      node.fixed = true
      onNodeSelect(node.id)
      heatRef.current = 0.95
      return
    }
    onNodeSelect(null)
  }, [findNodeAt, onNodeSelect])

  const handlePointerMove = useCallback((event: PointerEvent<HTMLCanvasElement>) => {
    const pointer = pointerRef.current
    if (!pointer.mode) {
      const node = findNodeAt(event.clientX, event.clientY)
      event.currentTarget.style.cursor = node ? 'grab' : 'move'
      return
    }
    if (pointer.mode === 'pan') {
      const transform = transformRef.current
      transform.x += event.clientX - pointer.lastX
      transform.y += event.clientY - pointer.lastY
    } else if (pointer.nodeId) {
      const node = nodesRef.current.find((item) => item.id === pointer.nodeId)
      const world = screenToWorld(event.clientX, event.clientY)
      if (node) {
        node.x = world.x
        node.y = world.y
        node.vx = 0
        node.vy = 0
        heatRef.current = 0.95
      }
    }
    pointer.lastX = event.clientX
    pointer.lastY = event.clientY
  }, [findNodeAt, screenToWorld])

  const handlePointerUp = useCallback((event: PointerEvent<HTMLCanvasElement>) => {
    const pointer = pointerRef.current
    if (pointer.nodeId) {
      const node = nodesRef.current.find((item) => item.id === pointer.nodeId)
      if (node) node.fixed = false
      heatRef.current = Math.max(heatRef.current, 0.55)
    }
    pointerRef.current = { mode: null, nodeId: null, lastX: 0, lastY: 0 }
    event.currentTarget.releasePointerCapture(event.pointerId)
  }, [])

  const handleDoubleClick = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    transformRef.current = fitCanvasNetwork(nodesRef.current, canvas.clientWidth, canvas.clientHeight)
  }, [])

  return (
    <div ref={containerRef} className="h-full w-full">
      <canvas
        ref={canvasRef}
        className="h-full w-full touch-none"
        onDoubleClick={handleDoubleClick}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      />
    </div>
  )
}

function defaultNodeLimit(networkKind: CanvasNetworkKind, nodeCount: number): number {
  if (networkKind === 'scene') return Math.min(180, nodeCount)
  return Math.min(260, nodeCount)
}

function effectiveMinWeight(networkKind: CanvasNetworkKind, minWeightValue: number): number {
  return networkKind === 'scene' ? minWeightValue / 100 : minWeightValue
}

function filterCanvasNetwork(
  nodes: BehaviorNetworkNode[],
  edges: BehaviorNetworkEdge[],
  networkKind: CanvasNetworkKind,
  settings: CanvasNetworkSettings
): { nodes: BehaviorNetworkNode[]; edges: BehaviorNetworkEdge[] } {
  const search = settings.search.trim().toLowerCase()
  const limit = settings.nodeLimit || defaultNodeLimit(networkKind, nodes.length)
  const minWeight = effectiveMinWeight(networkKind, settings.minWeightValue)
  const connectedIds = new Set(edges.flatMap((edge) => [edge.source, edge.target]))
  const searchableText = (node: BehaviorNetworkNode) => `${node.id}\n${node.data.label}\n${node.data.detail}\n${node.data.metric}`.toLowerCase()
  let visibleNodes = nodes.slice()
  if (networkKind === 'tag' && settings.onlyConnected) {
    visibleNodes = visibleNodes.filter((node) => connectedIds.has(node.id))
  }
  if (search) {
    visibleNodes = visibleNodes.filter((node) => searchableText(node).includes(search))
  }
  const chosenIds = new Set(visibleNodes.slice(0, limit).map((node) => node.id))
  const visibleEdges = edges.filter((edge) => (
    chosenIds.has(edge.source) &&
    chosenIds.has(edge.target) &&
    edgeWeight(edge) >= minWeight
  ))
  const linkedIds = new Set(visibleEdges.flatMap((edge) => [edge.source, edge.target]))
  const finalNodes = visibleNodes
    .slice(0, limit)
    .filter((node) => linkedIds.has(node.id) || search)
  return { nodes: finalNodes, edges: visibleEdges }
}

function NetworkShell({
  title,
  description,
  loading,
  empty,
  nodes,
  edges,
  networkKind,
  selectedNodeId,
  onNodeSelect,
  detail,
}: {
  title: string
  description: string
  loading: boolean
  empty: boolean
  nodes: BehaviorNetworkNode[]
  edges: BehaviorNetworkEdge[]
  networkKind: CanvasNetworkKind
  selectedNodeId: string | null
  onNodeSelect: (nodeId: string | null) => void
  detail: ReactNode
}) {
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [layoutVersion, setLayoutVersion] = useState(0)
  const [settings, setSettings] = useState<CanvasNetworkSettings>(DEFAULT_CANVAS_NETWORK_SETTINGS)
  const filteredGraph = useMemo(
    () => filterCanvasNetwork(nodes, edges, networkKind, settings),
    [edges, networkKind, nodes, settings]
  )
  const maxWeightValue = useMemo(() => {
    const maxWeight = Math.max(1, ...edges.map((edge) => edgeWeight(edge)))
    return networkKind === 'scene' ? Math.ceil(maxWeight * 100) : Math.ceil(maxWeight)
  }, [edges, networkKind])
  const nodeLimit = settings.nodeLimit || defaultNodeLimit(networkKind, nodes.length)
  const minNodeLimit = networkKind === 'scene' ? 20 : 40
  const updateSetting = <Key extends keyof CanvasNetworkSettings>(key: Key, value: CanvasNetworkSettings[Key]) => {
    setSettings((current) => ({ ...current, [key]: value }))
  }

  return (
    <div className="overflow-hidden rounded-lg border bg-background">
      <div className="flex flex-col gap-1 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-sm font-semibold">{title}</h2>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline">{filteredGraph.nodes.length} 节点</Badge>
          <Badge variant="outline">{filteredGraph.edges.length} 连线</Badge>
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
        </div>
      </div>
      {empty ? (
        <div className="p-8 text-center text-sm text-muted-foreground">暂无图谱数据</div>
      ) : (
        <div className="relative h-[680px] overflow-hidden bg-muted/10">
          <div
            className={cn(
              'absolute left-3 top-3 z-20 w-[min(320px,calc(100%-1.5rem))] transition duration-200',
              settingsOpen ? 'translate-x-0' : '-translate-x-[calc(100%-2.5rem)]'
            )}
          >
            <div className="overflow-hidden rounded-md border bg-background/95 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-background/88">
              <div className="flex items-center justify-between border-b px-3 py-2">
                <span className="text-sm font-medium">图谱调节</span>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setSettingsOpen((open) => !open)}
                  aria-label={settingsOpen ? '收起图谱调节' : '展开图谱调节'}
                >
                  <ChevronRight className={cn('h-4 w-4 transition', settingsOpen && 'rotate-180')} />
                </Button>
              </div>
              <div className={cn('grid gap-3 p-3', settingsOpen ? 'block' : 'hidden')}>
                <div className="grid gap-1 text-xs text-muted-foreground">
                  <Label htmlFor={`behavior-network-search-${networkKind}`}>搜索</Label>
                  <Input
                    id={`behavior-network-search-${networkKind}`}
                    value={settings.search}
                    onChange={(event) => updateSetting('search', event.target.value)}
                    placeholder={networkKind === 'scene' ? '名称、ID、tag 或 session' : 'tag 或 cluster_key'}
                  />
                </div>
                <label className="grid gap-1 text-xs text-muted-foreground">
                  {networkKind === 'scene'
                    ? `最小重叠度 ${effectiveMinWeight(networkKind, settings.minWeightValue).toFixed(2)}`
                    : `最小边权重 ${settings.minWeightValue.toFixed(0)}`}
                  <input
                    type="range"
                    min={0}
                    max={maxWeightValue}
                    step={1}
                    value={settings.minWeightValue}
                    onChange={(event) => updateSetting('minWeightValue', Number(event.target.value))}
                  />
                </label>
                <label className="grid gap-1 text-xs text-muted-foreground">
                  最多显示节点 {nodeLimit}
                  <input
                    type="range"
                    min={minNodeLimit}
                    max={Math.max(minNodeLimit, nodes.length)}
                    step={1}
                    value={nodeLimit}
                    onChange={(event) => {
                      updateSetting('nodeLimit', Number(event.target.value))
                      setLayoutVersion((value) => value + 1)
                    }}
                  />
                </label>
                {networkKind === 'tag' && (
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={settings.onlyConnected}
                      onChange={(event) => updateSetting('onlyConnected', event.target.checked)}
                    />
                    只显示参与场景分布的 tag 簇
                  </label>
                )}
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={settings.showLabels}
                    onChange={(event) => updateSetting('showLabels', event.target.checked)}
                  />
                  显示标签
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={settings.paused}
                    onChange={(event) => updateSetting('paused', event.target.checked)}
                  />
                  暂停
                </label>
                <div className="flex gap-2">
                  <Button variant="outline" size="sm" onClick={() => setLayoutVersion((value) => value + 1)}>
                    重新布局
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setSettings(DEFAULT_CANVAS_NETWORK_SETTINGS)
                      setLayoutVersion((value) => value + 1)
                    }}
                  >
                    重置
                  </Button>
                </div>
              </div>
            </div>
          </div>
          <BehaviorNetworkCanvas
            nodes={filteredGraph.nodes}
            edges={filteredGraph.edges}
            networkKind={networkKind}
            settings={settings}
            layoutVersion={layoutVersion}
            selectedNodeId={selectedNodeId}
            onNodeSelect={onNodeSelect}
          />
          <div
            data-open={detail ? 'true' : 'false'}
            className={cn(
              'absolute bottom-4 right-4 top-4 w-[min(420px,calc(100%-2rem))] transition duration-200 ease-out',
              detail ? 'pointer-events-auto translate-x-0 opacity-100' : 'pointer-events-none translate-x-[calc(100%+2rem)] opacity-0'
            )}
          >
            <div className="flex h-full flex-col overflow-hidden rounded-lg border bg-background/96 shadow-2xl backdrop-blur-md supports-[backdrop-filter]:bg-background/88">
              <div className="flex items-center justify-between border-b bg-background/95 px-4 py-3 backdrop-blur-md">
                <div>
                  <h2 className="text-sm font-semibold">节点详情</h2>
                  <p className="text-xs text-muted-foreground">分布、成员和统计信息</p>
                </div>
                <Button variant="ghost" size="icon" onClick={() => onNodeSelect(null)} aria-label="关闭节点详情">
                  <X className="h-4 w-4" />
                </Button>
              </div>
              <ScrollArea className="min-h-0 flex-1 bg-background/90 p-4 backdrop-blur-md">
                {detail}
              </ScrollArea>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function SceneNetworkDetail({ node }: { node: BehaviorGraphData['scene_cluster_network']['nodes'][number] }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border bg-muted/20 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">场景簇 #{node.id}</Badge>
          <Badge variant="outline">{node.session_id}</Badge>
        </div>
        <h3 className="break-words text-sm font-semibold leading-6">{node.label}</h3>
        <p className="mt-1 text-xs text-muted-foreground">更新 {formatTime(node.update_time)}</p>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Metric label="路径" value={String(node.path_count)} />
        <Metric label="样本" value={String(node.source_count)} />
        <Metric label="使用" value={String(node.activation_count)} />
        <Metric label="正向" value={String(node.success_count)} />
      </div>
      <Panel title="Tag 分布">
        <ReadableTagList tags={node.tags} />
      </Panel>
    </div>
  )
}

function TagNetworkDetail({ node }: { node: BehaviorGraphData['tag_network']['nodes'][number] }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border bg-muted/20 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <Badge variant="secondary">{node.kind || 'tag'}</Badge>
          <Badge variant="outline">{node.scene_count} 场景簇</Badge>
        </div>
        <h3 className="break-words text-sm font-semibold leading-6">{node.label}</h3>
        <p className="mt-1 break-all font-mono text-xs text-muted-foreground">{node.id}</p>
      </div>
      <div className="grid grid-cols-2 gap-2 text-xs">
        <Metric label="场景簇" value={String(node.scene_count)} />
        <Metric label="成员样本" value={String(node.source_count)} />
        <Metric label="权重" value={node.weight.toFixed(3)} />
        <Metric label="成员" value={String(node.aliases.length)} />
      </div>
      <Panel title="Tag 簇成员">
        {node.aliases.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无成员</p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {node.aliases.map((alias) => (
              <Badge key={alias} variant="outline" className="max-w-full whitespace-normal break-all">
                {alias}
              </Badge>
            ))}
          </div>
        )}
      </Panel>
    </div>
  )
}

function SceneClusterCard({ cluster }: { cluster: BehaviorClusterItem }) {
  const visibleTags = topClusterTags(cluster.tags, 8)
  return (
    <div className="min-w-0 rounded-lg border bg-background p-3 transition hover:border-primary/40">
      <div className="mb-3 flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            {cluster.id !== null && <Badge variant="secondary">#{cluster.id}</Badge>}
            <Badge variant="outline">{cluster.chat_name || cluster.session_id || '全局行为'}</Badge>
          </div>
          <h3 className="line-clamp-2 break-words text-sm font-semibold leading-6">
            {clusterTitle(cluster.name, cluster.tags)}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">更新 {formatTime(cluster.update_time)}</p>
        </div>
        <div className="grid shrink-0 grid-cols-2 gap-1 text-right text-[11px] text-muted-foreground">
          <span>{cluster.path_count} 路径</span>
          <span>{cluster.source_count} 样本</span>
          <span>{cluster.activation_count} 使用</span>
          <span>{cluster.enabled_path_count} 启用</span>
        </div>
      </div>
      <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
        <Metric label="启用" value={String(cluster.enabled_path_count)} />
        <Metric label="观察" value={String(cluster.observed_path_count)} />
        <Metric label="自身" value={String(cluster.self_reflection_path_count)} />
      </div>
      <div className="mb-3 grid grid-cols-3 gap-2 text-xs">
        <Metric label="正向" value={String(cluster.success_count)} />
        <Metric label="负向" value={String(cluster.failure_count)} />
        <Metric label="最近使用" value={formatTime(cluster.last_active_time)} />
      </div>
      {visibleTags.length === 0 ? (
        <p className="text-sm text-muted-foreground">暂无 tag 分布</p>
      ) : (
        <div className="space-y-2">
          {visibleTags.map((item) => (
            <div key={item.tag} className="grid gap-2 text-sm sm:grid-cols-[minmax(0,1fr)_4rem] sm:items-center">
              <div className="min-w-0">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="break-words text-xs text-foreground">{tagDisplayText(item)}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatProbability(item.probability)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-primary" style={{ width: formatProbability(item.probability) }} />
                </div>
              </div>
              <span className="hidden text-right text-xs text-muted-foreground sm:block">{item.probability.toFixed(3)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ReadableTagList({ tags }: { tags: BehaviorGraphData['scene_cluster_network']['nodes'][number]['tags'] }) {
  if (tags.length === 0) return <p className="text-sm text-muted-foreground">暂无 tag 分布</p>
  return (
    <div className="space-y-2">
      {tags.slice(0, 12).map((tag) => (
        <div key={tag.tag} className="grid gap-2 text-sm sm:grid-cols-[minmax(0,1fr)_4rem] sm:items-center">
          <div className="min-w-0">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="break-words text-xs text-foreground">{tag.display}</span>
              <span className="shrink-0 text-xs text-muted-foreground">{formatProbability(tag.probability)}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: formatProbability(tag.probability) }} />
            </div>
          </div>
          <span className="hidden text-right text-xs text-muted-foreground sm:block">{tag.probability.toFixed(3)}</span>
        </div>
      ))}
    </div>
  )
}

function SceneGroupRow({
  group,
  open,
  selectedPathId,
  onToggle,
  onSelectPath,
}: {
  group: BehaviorSceneGroup
  open: boolean
  selectedPathId: number | null
  onToggle: () => void
  onSelectPath: (pathId: number) => void
}) {
  const title = clusterTitle(group.clusterName, group.clusterTags)
  const selfReflectionPaths = group.paths.filter(isSelfReflectionPath)
  const selfSuccessCount = selfReflectionPaths.reduce((sum, path) => sum + path.success_count, 0)
  const selfFailureCount = selfReflectionPaths.reduce((sum, path) => sum + path.failure_count, 0)
  return (
    <Collapsible open={open} onOpenChange={onToggle}>
      <div className="px-4 py-3">
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full flex-col gap-3 rounded-lg p-2 text-left transition hover:bg-muted/60 lg:flex-row lg:items-start lg:justify-between"
          >
            <div className="min-w-0 flex-1 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                {open ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                )}
                <Badge variant="outline">{group.paths.length} 个行为分支</Badge>
                {group.sceneClusterId !== null && <Badge variant="secondary">场景簇 #{group.sceneClusterId}</Badge>}
                <span className="text-xs text-muted-foreground">{group.chatName}</span>
                <span className="text-xs text-muted-foreground">更新 {formatTime(group.latestUpdate)}</span>
              </div>
              <p className="text-sm leading-6">
                <span className="text-muted-foreground">触发分布：</span>
                {shortText(title, 130)}
              </p>
              <ClusterTagPills tags={group.clusterTags} maxCount={5} />
            </div>
            <div className={cn(
              'grid min-w-[220px] gap-2 text-center text-xs',
              selfReflectionPaths.length > 0 ? 'grid-cols-4' : 'grid-cols-2'
            )}>
              <Metric label="最高分" value={formatScore(group.bestScore)} />
              <Metric label="使用" value={String(group.activationCount)} />
              {selfReflectionPaths.length > 0 && <Metric label="正向" value={String(selfSuccessCount)} />}
              {selfReflectionPaths.length > 0 && <Metric label="负向" value={String(selfFailureCount)} />}
            </div>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="mt-2 space-y-2 border-l pl-4">
            <ClusterDistributionPanel
              name={group.clusterName}
              tags={group.clusterTags}
              sourceCount={group.clusterSourceCount}
              compact
            />
            {group.paths.map((path) => {
              const isSelfPath = isSelfReflectionPath(path)
              return (
                <button
                  key={path.id}
                  type="button"
                  onClick={() => onSelectPath(path.id)}
                  className={cn(
                    'block w-full rounded-lg border bg-background px-3 py-3 text-left transition hover:bg-muted/60',
                    selectedPathId === path.id && 'border-primary bg-muted'
                  )}
                >
                  <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0 flex-1 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={path.enabled ? 'default' : 'secondary'}>{path.enabled ? '启用' : '停用'}</Badge>
                        <Badge variant={isSelfPath ? 'default' : 'outline'}>
                          {behaviorPathTypeLabel(path)}
                        </Badge>
                        <span className="text-xs text-muted-foreground">经验路径 #{path.id}</span>
                        <span className="text-xs text-muted-foreground">更新 {formatTime(path.update_time)}</span>
                      </div>
                      <p className="text-sm"><span className="text-muted-foreground">行为：</span>{shortText(path.action, 110)}</p>
                      <p className="text-sm"><span className="text-muted-foreground">结果：</span>{shortText(path.outcome, 110)}</p>
                    </div>
                    <div className={cn(
                      'grid min-w-[220px] gap-2 text-center text-xs',
                      isSelfPath ? 'grid-cols-5' : 'grid-cols-3'
                    )}>
                      <Metric label="分数" value={formatScore(path.score)} />
                      <Metric label="样本" value={String(path.count)} />
                      <Metric label="使用" value={String(path.activation_count)} />
                      {isSelfPath && <Metric label="正向" value={String(path.success_count)} />}
                      {isSelfPath && <Metric label="负向" value={String(path.failure_count)} />}
                    </div>
                  </div>
                </button>
              )
            })}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border bg-muted/30 px-2 py-1">
      <div className="font-medium text-foreground">{value}</div>
      <div className="text-muted-foreground">{label}</div>
    </div>
  )
}

function ClusterTagPills({ tags, maxCount = 6 }: { tags: BehaviorClusterTag[]; maxCount?: number }) {
  const visibleTags = topClusterTags(tags, maxCount)
  if (visibleTags.length === 0) return null
  return (
    <div className="flex flex-wrap gap-1.5">
      {visibleTags.map((item) => (
        <Badge key={item.tag} variant="outline" className="max-w-full whitespace-normal break-all text-[11px]">
          {tagDisplayText(item)} · {formatProbability(item.probability)}
        </Badge>
      ))}
    </div>
  )
}

function ClusterDistributionPanel({
  name,
  tags,
  sourceCount,
  compact = false,
}: {
  name: string
  tags: BehaviorClusterTag[]
  sourceCount?: number
  compact?: boolean
}) {
  const visibleTags = topClusterTags(tags, compact ? 8 : 12)
  return (
    <div className={cn('rounded-lg border bg-muted/20', compact ? 'p-3' : 'p-4')}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">场景簇</span>
        {sourceCount !== undefined && <Badge variant="outline">样本 {sourceCount}</Badge>}
      </div>
      <p className="mb-3 break-words text-sm font-medium leading-6">{clusterTitle(name, tags)}</p>
      {visibleTags.length === 0 ? (
        <p className="text-sm text-muted-foreground">{name || '暂无 tag 分布'}</p>
      ) : (
        <div className="space-y-2">
          {visibleTags.map((item) => (
            <div key={item.tag} className="grid gap-2 text-sm sm:grid-cols-[minmax(0,1fr)_4rem] sm:items-center">
              <div className="min-w-0">
                <div className="mb-1 flex items-center justify-between gap-2">
                  <span className="break-words text-xs text-foreground">{tagDisplayText(item)}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">{formatProbability(item.probability)}</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className="h-full rounded-full bg-primary" style={{ width: formatProbability(item.probability) }} />
                </div>
              </div>
              <span className="hidden text-right text-xs text-muted-foreground sm:block">{item.probability.toFixed(3)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

function RetrievalDebugView({ result }: { result: BehaviorRetrievalDebugPayload | null }) {
  if (!result) {
    return (
      <div className="rounded-lg border bg-background p-8 text-center text-sm text-muted-foreground">
        输入场景画像后，可以看到命中的场景簇、检索调试信息和候选经验路径
      </div>
    )
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {result.error && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive lg:col-span-2">
          {result.error}
        </div>
      )}
      {result.scenario_profile && (
        <Panel title={result.input_mode === 'llm_scene_text' ? 'LLM 场景画像' : '手动场景画像'}>
          <div className="space-y-3 text-sm">
            {result.scenario_profile.summary && (
              <p className="text-muted-foreground">{result.scenario_profile.summary}</p>
            )}
            <div className="flex flex-wrap gap-2">
              {result.scenario_profile.tag_clusters.length === 0 ? (
                <span className="text-muted-foreground">没有生成 tag 簇</span>
              ) : (
                result.scenario_profile.tag_clusters.map((cluster, index) => (
                  <Badge key={`${cluster.kind}-${index}`} variant="outline" className="max-w-full whitespace-normal break-all">
                    {scenarioTagKindLabel(cluster.kind)}：{cluster.tags.join(' / ')}
                  </Badge>
                ))
              )}
            </div>
          </div>
        </Panel>
      )}
      <Panel title="检索概览">
        <div className="grid gap-2 text-sm sm:grid-cols-2">
          <Metric label="检索模式" value={retrievalModeLabel(result.retrieval_mode)} />
          <Metric label="候选分数" value={`${result.candidate_scores.length} 条`} />
          <Metric label="命中场景簇" value={`${result.matched_clusters.length} 个`} />
          <Metric label="描述符" value={`${result.descriptors.length} 个`} />
        </div>
      </Panel>
      <Panel title="命中场景簇">
        <ClusterScoreList clusters={result.matched_clusters} />
      </Panel>
      <Panel title="直接重叠调试">
        <RetrievalStageDebug stage={result.retrieval_debug.direct} />
      </Panel>
      <Panel title="Tag 簇扩散调试">
        <RetrievalStageDebug stage={result.retrieval_debug.spread} />
        <RetrievalLockDebug debug={result.retrieval_debug} />
      </Panel>
      <Panel title="候选路径">
        <div className="space-y-3">
          {result.candidates.length === 0 ? (
            <p className="text-sm text-muted-foreground">没有命中候选</p>
          ) : (
            result.candidates.map((candidate) => (
              <div key={candidate.behavior_id} className="rounded-md border p-3 text-sm">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-medium">#{candidate.behavior_id}</span>
                  <Badge variant="outline">{formatScore(candidate.score)}</Badge>
                </div>
                {candidate.path ? (
                  <div className="space-y-1 text-muted-foreground">
                    <p>场景簇：{shortText(clusterTitle(candidate.path.scene_cluster_name, candidate.path.scene_cluster_tags), 56)}</p>
                    <p>行为：{shortText(candidate.path.action, 56)}</p>
                  </div>
                ) : (
                  <p className="text-muted-foreground">路径已不存在</p>
                )}
              </div>
            ))
          )}
        </div>
      </Panel>
    </div>
  )
}

function scenarioTagKindLabel(kind: string): string {
  if (kind === 'domain') return '领域'
  if (kind === 'need') return '需求'
  if (kind === 'attitude') return '他人特点/态度'
  return kind
}

function retrievalModeLabel(mode: string): string {
  const labels: Record<string, string> = {
    direct_domain_overlap: '直接领域重叠',
    tag_cluster_spread_1: 'Tag 簇一跳扩散',
    tag_cluster_spread_2: 'Tag 簇两跳扩散',
  }
  return labels[mode] ?? mode
}

function RetrievalStageDebug({ stage }: { stage: BehaviorRetrievalDebugPayload['retrieval_debug']['direct'] }) {
  if (!stage) return <p className="text-sm text-muted-foreground">当前检索模式未产生这部分调试信息</p>
  const hopCounts = stage.hop_counts ? Object.entries(stage.hop_counts) : []
  return (
    <div className="space-y-3">
      <div className="grid gap-2 text-sm sm:grid-cols-2">
        <Metric label="直接 tag 数" value={String(stage.direct_tag_count)} />
        {'expanded_tag_count' in stage && <Metric label="扩展 tag 数" value={String(stage.expanded_tag_count ?? 0)} />}
        {'total_query_tag_count' in stage && <Metric label="查询 tag 总数" value={String(stage.total_query_tag_count ?? 0)} />}
        <Metric label="命中场景簇" value={String(stage.cluster_count)} />
      </div>
      {hopCounts.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {hopCounts.map(([hop, count]) => (
            <Badge key={hop} variant="outline">
              {hop === '0' ? '直接' : `${hop} 跳`}：{count}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

function RetrievalLockDebug({ debug }: { debug: BehaviorRetrievalDebugPayload['retrieval_debug'] }) {
  if (debug.direct_top_score === undefined) return null
  return (
    <div className="mt-3 grid gap-2 text-sm sm:grid-cols-2">
      <Metric label="直接最高分" value={formatScore(debug.direct_top_score)} />
      <Metric label="直接锁定" value={debug.direct_locked ? '是' : '否'} />
      {debug.direct_lock_threshold !== undefined && (
        <Metric label="锁定阈值" value={formatScore(debug.direct_lock_threshold)} />
      )}
      {debug.locked_direct_spread_factor !== undefined && (
        <Metric label="锁定后扩散系数" value={formatScore(debug.locked_direct_spread_factor)} />
      )}
    </div>
  )
}

function getBehaviorGraphNodeId(kind: string, id: number): string {
  if (kind === 'action') return `action:${id}`
  if (kind === 'outcome') return `outcome:${id}`
  if (kind === 'path') return `path:${id}`
  return `scene:${id}`
}

function hashBehaviorGraphText(value: string): number {
  return Array.from(value).reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) % 997, 17)
}

function getSceneNodeColumn(kind: string): number {
  if (kind === 'scene') return 0
  if (kind === 'intent' || kind === 'phase') return 1
  if (kind === 'domain' || kind === 'need') return 2
  return 3
}

function getSceneNodeLane(kind: string): number {
  const laneByKind: Record<string, number> = {
    scene: 0,
    intent: -1,
    phase: 1,
    domain: -1,
    need: 1,
    risk: -1,
  }
  return laneByKind[kind] ?? 0
}

function shouldShowBehaviorEdgeLabel(kind: string): boolean {
  return kind === 'scene_action' || kind === 'action_outcome'
}

function buildBehaviorFlowGraph(detail: BehaviorPathDetail): { nodes: BehaviorFlowNode[]; edges: BehaviorFlowEdge[] } {
  const detailNodes = detail.nodes
  const detailEdges = detail.edges
  const sceneNodes = detailNodes.filter((node) => node.kind !== 'action' && node.kind !== 'outcome')
  const actionNodes = detailNodes.filter((node) => node.kind === 'action')
  const outcomeNodes = detailNodes.filter((node) => node.kind === 'outcome')
  const layeredNodes = [
    ...sceneNodes,
    {
      id: detail.path.id,
      kind: 'path',
      label: `经验路径 #${detail.path.id}`,
      score: detail.path.score,
      source_count: detail.path.count,
    },
    ...actionNodes,
    ...outcomeNodes,
  ]
  const sceneColumnCounts = new Map<number, number>()
  sceneNodes.forEach((node) => {
    const column = getSceneNodeColumn(node.kind)
    sceneColumnCounts.set(column, (sceneColumnCounts.get(column) ?? 0) + 1)
  })
  const sceneColumnIndexes = new Map<number, number>()
  const actionOutcomeIndexes = new Map<string, number>()

  const nodes: BehaviorFlowNode[] = layeredNodes.map((node) => {
    let x = 0
    let y = 0

    if (node.kind === 'path') {
      x = 720
      y = -28
    } else if (node.kind === 'action' || node.kind === 'outcome') {
      const index = actionOutcomeIndexes.get(node.kind) ?? 0
      actionOutcomeIndexes.set(node.kind, index + 1)
      const count = node.kind === 'action' ? actionNodes.length : outcomeNodes.length
      const centeredIndex = index - (count - 1) / 2
      x = node.kind === 'action' ? 1030 : 1340
      y = centeredIndex * 150 + 18
    } else {
      const column = getSceneNodeColumn(node.kind)
      const index = sceneColumnIndexes.get(column) ?? 0
      const count = sceneColumnCounts.get(column) ?? 1
      sceneColumnIndexes.set(column, index + 1)
      const centeredIndex = index - (count - 1) / 2
      const hash = hashBehaviorGraphText(`${node.kind}:${node.id}:${node.label}`)
      const jitterX = (hash % 5) * 10
      const jitterY = ((hash % 7) - 3) * 8
      x = column * 190 + jitterX
      y = centeredIndex * 128 + getSceneNodeLane(node.kind) * 58 + jitterY
    }

    return {
      id: getBehaviorGraphNodeId(node.kind, node.id),
      type: 'behavior',
      position: { x, y },
      data: {
        kind: node.kind,
        label: shortText(node.label, node.kind === 'path' ? 36 : 72),
        detail: node.label,
      },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
    }
  })

  const nodeIds = new Set(nodes.map((node) => node.id))
  const edges: BehaviorFlowEdge[] = detailEdges
    .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
    .map((edge) => {
      const color = edge.kind === 'action_outcome'
        ? '#0284c7'
        : edge.kind === 'scene_action'
          ? '#059669'
          : edge.kind === 'co_occurs'
            ? '#94a3b8'
            : '#7c3aed'
      return {
        id: edge.id,
        source: edge.source,
        target: edge.target,
        type: edge.kind === 'co_occurs' ? 'straight' : 'bezier',
        animated: edge.kind === 'scene_action' || edge.kind === 'action_outcome',
        label: shouldShowBehaviorEdgeLabel(edge.kind) ? `${edge.kind} · ${formatScore(edge.weight)}` : undefined,
        interactionWidth: 18,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 14,
          height: 14,
          color,
        },
        style: {
          stroke: color,
          strokeWidth: Math.max(1.5, Math.min(4, edge.weight)),
          opacity: edge.kind === 'co_occurs' ? 0.25 : shouldShowBehaviorEdgeLabel(edge.kind) ? 0.82 : 0.48,
        },
        labelStyle: {
          fill: '#334155',
          fontSize: 11,
          fontWeight: 600,
        },
        labelBgPadding: [6, 2],
        labelBgBorderRadius: 6,
        labelBgStyle: { fill: 'rgba(255,255,255,0.92)', fillOpacity: 0.95 },
      }
    })

  return { nodes, edges }
}

function BehaviorFlowGraph({ detail }: { detail: BehaviorPathDetail }) {
  const { nodes, edges } = useMemo(() => buildBehaviorFlowGraph(detail), [detail])
  if (nodes.length === 0) {
    return <div className="rounded-lg border p-6 text-center text-sm text-muted-foreground">暂无可视化节点</div>
  }
  return (
    <div className="h-[640px] overflow-hidden rounded-lg border bg-background">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={behaviorNodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.25}
        maxZoom={1.4}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
        attributionPosition="bottom-left"
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls />
      </ReactFlow>
    </div>
  )
}

function PathGraphView({ detail, loading }: { detail: BehaviorPathDetail | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="rounded-lg border bg-background p-8 text-center text-sm text-muted-foreground">
        <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
        正在读取局部图谱
      </div>
    )
  }
  if (!detail) {
    return <div className="rounded-lg border bg-background p-8 text-center text-sm text-muted-foreground">先选择一条经验路径</div>
  }
  const isSelfPath = isSelfReflectionPath(detail.path)
  const sceneClusterTags = detail.scene_cluster.tags
  const pathClusterTags = detail.path.scene_cluster_tags
  const detailNodes = detail.nodes
  const detailEdges = detail.edges
  const evidence = detail.evidence
  const feedback = detail.feedback
  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4 rounded-lg border bg-background p-4">
        <div>
          <h2 className="text-base font-semibold">#{detail.path.id} {detail.path.chat_name}</h2>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={detail.path.learning_type === 'self_reflection' ? 'default' : 'outline'}>
              {behaviorPathTypeLabel(detail.path)}
            </Badge>
            <span className="text-sm text-muted-foreground">最近更新 {formatTime(detail.path.update_time)}</span>
          </div>
        </div>
        <ClusterDistributionPanel
          name={detail.scene_cluster.name || detail.path.scene_cluster_name}
          tags={sceneClusterTags.length > 0 ? sceneClusterTags : pathClusterTags}
          sourceCount={detail.scene_cluster.source_count || detail.path.scene_cluster_source_count}
        />
        <div className="grid gap-3 md:grid-cols-2">
          <PathBlock title="行为" content={detail.path.action} />
          <PathBlock title="结果" content={detail.path.outcome} />
        </div>
        <Panel title="节点图">
          <BehaviorFlowGraph detail={detail} />
        </Panel>
        <Panel title="节点">
          <div className="grid gap-2 md:grid-cols-2">
            {detailNodes.map((node, index) => (
              <div key={`${node.kind}-${node.id}-${index}`} className="rounded-md border p-3">
                <div className="mb-1 flex items-center gap-2">
                  <Badge variant="outline">{node.kind}</Badge>
                  <span className="text-xs text-muted-foreground">#{node.id}</span>
                </div>
                <p className="text-sm">{node.label}</p>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="边">
          <div className="space-y-2">
            {detailEdges.map((edge) => (
              <div key={edge.id} className="rounded-md border px-3 py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="secondary">{edge.kind}</Badge>
                  <span className="text-muted-foreground">{edge.source} → {edge.target}</span>
                  <span className="ml-auto text-xs">权重 {formatScore(edge.weight)} · {edge.count} 次</span>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
      <div className="space-y-4">
        <Panel title="证据">
          <JsonList items={evidence} />
        </Panel>
        {isSelfPath ? (
          <Panel title="反馈">
            <JsonList items={feedback} />
          </Panel>
        ) : (
          <Panel title="反馈">
            <p className="text-sm text-muted-foreground">观察学习路径不记录正向/负向反馈。</p>
          </Panel>
        )}
      </div>
    </div>
  )
}

function PathBlock({ title, content }: { title: string; content: string }) {
  return (
    <div className="rounded-lg border bg-muted/20 p-3">
      <div className="mb-2 text-xs font-medium text-muted-foreground">{title}</div>
      <p className="text-sm leading-6">{content || '-'}</p>
    </div>
  )
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border bg-background p-4">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {children}
    </section>
  )
}

function ClusterScoreList({ clusters }: { clusters: BehaviorRetrievalDebugPayload['matched_clusters'] }) {
  if (clusters.length === 0) return <p className="text-sm text-muted-foreground">暂无数据</p>
  return (
    <div className="space-y-2">
      {clusters.map((cluster) => (
        <div key={cluster.cluster_id} className="rounded-md border px-3 py-2 text-sm">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge variant="outline">#{cluster.cluster_id}</Badge>
            <span className="text-xs text-muted-foreground">匹配 {formatScore(cluster.score)}</span>
            <span className="text-xs text-muted-foreground">样本 {cluster.source_count}</span>
          </div>
          <p className="mb-2 break-words text-sm font-medium">{clusterTitle(cluster.name, cluster.tags)}</p>
          <ClusterTagPills tags={cluster.tags} maxCount={5} />
        </div>
      ))}
    </div>
  )
}

function JsonList({ items }: { items: unknown[] }) {
  if (items.length === 0) return <p className="text-sm text-muted-foreground">暂无记录</p>
  return (
    <div className="space-y-2">
      {items.slice().reverse().map((item, index) => (
        <pre key={index} className="overflow-auto rounded-md border bg-muted/30 p-3 text-xs leading-5">
          {JSON.stringify(item, null, 2)}
        </pre>
      ))}
    </div>
  )
}
