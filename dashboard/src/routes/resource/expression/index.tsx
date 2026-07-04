import {
  Check,
  Download,
  FileClock,
  MessageSquare,
  MoreHorizontal,
  Network,
  Plus,
  Search,
  Trash2,
  Upload,
  X,
  Zap,
} from 'lucide-react'
import { useRef, useState } from 'react'

import { useQuery } from '@tanstack/react-query'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { AccentPanel } from '@/components/ui/accent-panel'
import { Button } from '@/components/ui/button'
import { DashboardTabBar, DashboardTabTrigger } from '@/components/ui/dashboard-tabs'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ExpressionReviewer } from '@/components/expression-reviewer'
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
import { Switch } from '@/components/ui/switch'
import { Tabs } from '@/components/ui/tabs'
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'

import {
  batchDeleteExpressions,
  deleteExpression,
  getChatList,
  getExpressionDetail,
  getExpressionGroups,
  getExpressionList,
  getExpressionStats,
  getReviewStats,
} from '@/lib/expression-api'

import { useExpressionImportExport } from './hooks/useExpressionImportExport'
import { useExpressionReview } from './hooks/useExpressionReview'

import {
  BatchDeleteConfirmDialog,
  ClearChatExpressionsConfirmDialog,
  DeleteConfirmDialog,
  ExpressionCreateDialog,
  ExpressionDetailDialog,
  ExpressionEditDialog,
  LegacyExpressionImportDialog,
} from './ExpressionDialogs'
import { ExpressionClusterBrowser } from './ExpressionClusterBrowser'
import { ExpressionList } from './ExpressionList'
import { ExpressionReviewLogPanel } from './ExpressionReviewLogPanel'

import type {
  ChatInfo,
  Expression,
  ExpressionGroupInfo,
} from '@/types/expression'
import type { StatsData } from './types'

type IndicatorStatus = 'on' | 'off' | 'mixed'
type ExpressionReviewFilter = 'all' | 'user_checked' | 'unchecked'
type BrowseMode = 'chat' | 'group' | 'all'

interface ExpressionLearningScopeStatus {
  label: string
  useExpression: IndicatorStatus
  enableLearning: IndicatorStatus
}

/** useDataList 的筛选袋：浏览维度 + 旧格式开关 + 审核筛选 */
interface ExpressionFilters {
  browseMode: BrowseMode
  selectedChatId: string
  selectedGroupIndex: number | null
  showLegacyExpressions: boolean
  reviewFilter: ExpressionReviewFilter
}

const INITIAL_FILTERS: ExpressionFilters = {
  browseMode: 'chat',
  selectedChatId: '',
  selectedGroupIndex: null,
  showLegacyExpressions: false,
  reviewFilter: 'all',
}

const DEFAULT_STATS: StatsData = {
  total: 0,
  recent_7days: 0,
  chat_count: 0,
  top_chats: {},
}

/**
 * 表达方式管理主页面
 */
export function ExpressionManagementPage() {
  const { toast } = useToast()
  const importInputRef = useRef<HTMLInputElement>(null)

  // 浏览面板与对话框等纯 UI 局部态
  const [browserPanelCollapsed, setBrowserPanelCollapsed] = useState(false)
  const [selectedExpression, setSelectedExpression] = useState<Expression | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isLegacyImportOpen, setIsLegacyImportOpen] = useState(false)
  const [deleteConfirmExpression, setDeleteConfirmExpression] = useState<Expression | null>(null)
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
  const [isClearConfirmOpen, setIsClearConfirmOpen] = useState(false)
  const [activeView, setActiveView] = useState<'list' | 'logs' | 'quick' | 'clusters'>('list')

  // 兄弟读：聊天流 / 共享组 / 统计 / 审核统计统一以 'expression' 前缀分层，
  // list.invalidate() 失效 ['expression'] 前缀时一并刷新（读失败局部呈现，不弹全局 toast）

  // 列表：分页/搜索/筛选/多选统一由 useDataList 承载，翻页/改参自动重置页码并清空选中
  const list = useDataList<Expression, ExpressionFilters, number>({
    domain: 'expression',
    getId: (expression) => expression.id,
    initialFilters: INITIAL_FILTERS,
    searchDebounceMs: 0,
    queryFn: async ({ page, pageSize, search, filters }) => {
      // 按共享组浏览时取该组的 chat_ids（全局组传 undefined，让后端返回全部）
      const selectedGroup =
        filters.browseMode === 'group'
          ? groups.find((group) => group.index === filters.selectedGroupIndex)
          : undefined
      // 已选中的非全局组但无任何聊天：直接返回空，避免无意义请求
      if (selectedGroup && !selectedGroup.is_global && selectedGroup.chat_ids.length === 0) {
        return { items: [], total: 0 }
      }
      const response = await getExpressionList({
        page,
        page_size: pageSize,
        search: search || undefined,
        chat_id:
          filters.browseMode === 'chat' ? filters.selectedChatId || undefined : undefined,
        chat_ids:
          selectedGroup && !selectedGroup.is_global ? selectedGroup.chat_ids : undefined,
        include_legacy: filters.showLegacyExpressions,
        review_filter: filters.reviewFilter,
      })
      return { items: response.data, total: response.total }
    },
  })

  const filters = list.filters
  const { browseMode, selectedChatId, selectedGroupIndex, showLegacyExpressions, reviewFilter } =
    filters

  // 统计：失败时保持占位数值，不打断页面；与列表同领域，list.invalidate() 会一并失效
  const statsQuery = useQuery({
    queryKey: ['expression', 'stats', { include_legacy: showLegacyExpressions }],
    queryFn: () =>
      getExpressionStats({ include_legacy: showLegacyExpressions }),
  })
  const stats: StatsData = statsQuery.data ?? DEFAULT_STATS

  // 审核统计：uncheckedCount 由此派生
  const reviewStatsQuery = useQuery({
    queryKey: ['expression', 'review-stats'],
    queryFn: () => getReviewStats(),
  })
  const uncheckedCount = reviewStatsQuery.data?.unchecked ?? 0

  // 聊天流列表（随「显示旧格式」开关刷新，保持原页面 loadChatList 的 include_legacy 行为）
  const chatListQuery = useQuery({
    queryKey: ['expression', 'chats', { include_legacy: showLegacyExpressions }],
    queryFn: () =>
      getChatList({ include_legacy: showLegacyExpressions }),
  })
  const chatList: ChatInfo[] = chatListQuery.data ?? []
  const chatNameMap = new Map<string, string>()
  chatList.forEach((chat) => chatNameMap.set(chat.chat_id, chat.chat_name))

  // 表达共享组（随「显示旧格式」开关刷新）
  const groupsQuery = useQuery({
    queryKey: ['expression', 'groups', { include_legacy: showLegacyExpressions }],
    queryFn: () =>
      getExpressionGroups({ include_legacy: showLegacyExpressions }),
  })
  const groups: ExpressionGroupInfo[] = groupsQuery.data ?? []

  // 按聊天浏览时若未选中或选中项已失效，自动选第一个聊天。
  // 用「渲染期版本标记」模式（React 官方推荐）替代 effect 内 setState，避免级联渲染告警。
  const needsAutoSelectChat =
    browseMode === 'chat' &&
    chatList.length > 0 &&
    (!selectedChatId || !chatList.some((chat) => chat.chat_id === selectedChatId))
  if (needsAutoSelectChat) {
    list.setFilter('selectedChatId', chatList[0].chat_id)
  }

  // 列表写成功后失效 ['expression'] 前缀：刷新列表 + 统计 + 审核统计
  const refreshAll = list.invalidate

  // 审核（单条 / 批量）：写成功后 invalidate 重新拉取（服务端为准）
  const { toggleReviewStatus, batchReviewStatus } = useExpressionReview({ onChanged: refreshAll })

  // 当前选中的具体聊天（用于导入导出清除与作用域指示）
  const currentChat =
    browseMode === 'chat' && selectedChatId
      ? chatList.find((chat) => chat.chat_id === selectedChatId) ?? null
      : null

  // 导入 / 导出 / 清除：写成功后 invalidate 刷新
  const { exportSelectedExpressionsToFile, handleImportFileChange, clearCurrentChat } =
    useExpressionImportExport({
      currentChat,
      selectedIds: list.selectedIds,
      onChanged: refreshAll,
      onClearSelection: list.clearSelection,
      onCloseClearConfirm: () => setIsClearConfirmOpen(false),
    })

  // 顶部视图切换（表达 / 快速审核 / AI审核记录）
  const handleActiveViewChange = (view: 'list' | 'logs' | 'quick' | 'clusters') => {
    setActiveView(view)
    if (view === 'list') {
      list.refetch()
      statsQuery.refetch()
    }
    reviewStatsQuery.refetch()
  }

  const handleViewExpressionById = async (expressionId: number) => {
    try {
      const result = await getExpressionDetail(expressionId)
      setSelectedExpression(result)
      setIsDetailDialogOpen(true)
    } catch (error) {
      toast({
        title: '加载详情失败',
        description: error instanceof Error ? error.message : '无法加载表达方式详情',
        variant: 'destructive',
      })
    }
  }

  // 查看详情（事件驱动的读取，失败用 toast 反馈用户动作）
  const handleViewDetail = async (expression: Expression) => {
    await handleViewExpressionById(expression.id)
  }

  // 编辑表达方式
  const handleEdit = (expression: Expression) => {
    setSelectedExpression(expression)
    setIsEditDialogOpen(true)
  }

  // 删除表达方式（成功后 invalidate 刷新列表 + 统计）
  const handleDelete = async () => {
    if (!deleteConfirmExpression) return
    try {
      await deleteExpression(deleteConfirmExpression.id)
      toast({
        title: '删除成功',
        description: `已删除表达方式: ${deleteConfirmExpression.situation}`,
      })
      setDeleteConfirmExpression(null)
      refreshAll()
    } catch (error) {
      toast({
        title: '删除失败',
        description: error instanceof Error ? error.message : '无法删除表达方式',
        variant: 'destructive',
      })
    }
  }

  // 批量删除（成功后清空选中并 invalidate 刷新）
  const handleBatchDelete = async () => {
    try {
      await batchDeleteExpressions(Array.from(list.selectedIds))
      toast({
        title: '批量删除成功',
        description: `已删除 ${list.selectedCount} 个表达方式`,
      })
      list.clearSelection()
      setIsBatchDeleteDialogOpen(false)
      refreshAll()
    } catch (error) {
      toast({
        title: '批量删除失败',
        description: error instanceof Error ? error.message : '无法批量删除表达方式',
        variant: 'destructive',
      })
    }
  }

  // 页面跳转
  const handleJumpToPage = (jumpToPage: string) => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= list.totalPages) {
      list.goToPage(targetPage)
    }
  }

  // 浏览维度切换：按聊天 / 按共享组 / 全部（变更后 setFilter 自动重置页码并清空选中）
  const handleBrowseModeChange = (mode: BrowseMode) => {
    list.setFilter('browseMode', mode)
    if (mode === 'chat' && !selectedChatId && chatList.length > 0) {
      list.setFilter('selectedChatId', chatList[0].chat_id)
    }
    if (mode !== 'chat') {
      list.setFilter('selectedChatId', '')
    }
    if (mode === 'group' && selectedGroupIndex === null && groups.length > 0) {
      list.setFilter('selectedGroupIndex', groups[0].index)
    }
    if (mode !== 'group') {
      list.setFilter('selectedGroupIndex', null)
    }
  }

  const handleChatChange = (chatId: string) => {
    list.setFilter('selectedChatId', chatId)
  }

  const handleGroupChange = (groupIndex: number | null) => {
    list.setFilter('selectedGroupIndex', groupIndex)
  }

  const handleLegacyExpressionsChange = (checked: boolean) => {
    list.setFilter('showLegacyExpressions', checked)
    list.setFilter('selectedChatId', '')
    list.setFilter('selectedGroupIndex', null)
  }

  const summarizeStatus = (values: boolean[]): IndicatorStatus => {
    if (values.every(Boolean)) {
      return 'on'
    }
    if (values.every((value) => !value)) {
      return 'off'
    }
    return 'mixed'
  }

  const getScopeStatus = (): ExpressionLearningScopeStatus | null => {
    if (browseMode === 'chat' && selectedChatId) {
      const selectedChat = chatList.find((chat) => chat.chat_id === selectedChatId)
      if (!selectedChat) {
        return null
      }
      return {
        label: selectedChat.chat_name,
        useExpression: selectedChat.use_expression ? 'on' : 'off',
        enableLearning: selectedChat.enable_learning ? 'on' : 'off',
      }
    }

    if (browseMode === 'group' && selectedGroupIndex !== null) {
      const selectedGroup = groups.find((group) => group.index === selectedGroupIndex)
      if (!selectedGroup || selectedGroup.members.length === 0) {
        return null
      }
      return {
        label: selectedGroup.name,
        useExpression: summarizeStatus(
          selectedGroup.members.map((member) => member.use_expression)
        ),
        enableLearning: summarizeStatus(
          selectedGroup.members.map((member) => member.enable_learning)
        ),
      }
    }

    return null
  }

  const scopeStatus = getScopeStatus()
  const renderStatusIndicator = (label: string, status: IndicatorStatus, separated = true) => {
    const statusLabel = label.replace(/^开启/, '')
    const statusText =
      status === 'mixed'
        ? `部分${statusLabel}`
        : status === 'on'
          ? `开启${statusLabel}`
          : `关闭${statusLabel}`
    const dotClass =
      status === 'mixed' ? 'bg-amber-500' : status === 'on' ? 'bg-green-500' : 'bg-muted-foreground'

    return (
      <div className={`flex items-center gap-1.5 px-2 py-1 text-xs ${separated ? 'border-l' : ''}`}>
        <span className={`h-2 w-2 rounded-full ${dotClass}`} />
        <span className="font-medium leading-none">{statusText}</span>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden p-4 pb-6 sm:p-6">
      <div className="mb-4 flex shrink-0 flex-col gap-3 sm:mb-3">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <Tabs
            value={activeView === 'quick' ? 'quick' : activeView === 'clusters' ? 'clusters' : 'list'}
            onValueChange={(value) => handleActiveViewChange(value as 'list' | 'quick' | 'clusters')}
            className="-mx-1 w-[calc(100%+0.5rem)] px-1 sm:mx-0 sm:w-auto sm:p-0"
          >
            <DashboardTabBar className="h-10 sm:w-fit">
              <DashboardTabTrigger value="list" className="h-10 flex-1 gap-2 sm:h-9 sm:flex-none">
                <MessageSquare className="h-4 w-4" />
                <span>表达</span>
              </DashboardTabTrigger>
              <DashboardTabTrigger value="quick" className="h-10 flex-1 gap-2 sm:h-9 sm:flex-none">
                <Zap className="h-4 w-4" />
                <span>精选</span>
                {uncheckedCount > 0 && (
                  <span className="ml-0.5 rounded-full bg-orange-500 px-1.5 py-0.5 text-xs leading-none text-white">
                    {uncheckedCount > 99 ? '99+' : uncheckedCount}
                  </span>
                )}
              </DashboardTabTrigger>
              <DashboardTabTrigger value="clusters" className="h-10 flex-1 gap-2 sm:h-9 sm:flex-none">
                <Network className="h-4 w-4" />
                <span>聚类</span>
              </DashboardTabTrigger>
            </DashboardTabBar>
          </Tabs>
          {(activeView === 'list' || activeView === 'quick') && (
            <div className="grid grid-cols-1 gap-2 sm:flex sm:items-center lg:justify-end">
              {activeView === 'quick' && (
                <Button
                  variant="outline"
                  onClick={() => handleActiveViewChange('logs')}
                  className="h-10 justify-center gap-2 sm:h-9"
                >
                  <FileClock className="h-4 w-4" />
                  AI审核记录
                </Button>
              )}
              {activeView === 'list' && (
                <>
                  <Button
                    variant="outline"
                    onClick={() => setIsLegacyImportOpen(true)}
                    className="h-10 justify-center gap-2 sm:h-9"
                  >
                    <Upload className="h-4 w-4" />
                    从旧版本导入
                  </Button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <ScrollArea className={activeView === 'list' ? 'min-h-0 flex-1' : 'hidden'}>
        <div className="pr-3 pb-2 sm:pr-4">
          {/* 表达方式列表 */}
          <div
            className={`grid grid-cols-1 items-stretch gap-5 transition-[grid-template-columns] duration-200 sm:gap-4 lg:h-[calc(100vh-13rem)] lg:min-h-[30rem] ${
              browserPanelCollapsed
                ? 'lg:grid-cols-[3.25rem_minmax(0,1fr)]'
                : 'lg:grid-cols-[13.5rem_minmax(0,1fr)]'
            }`}
          >
            <div className="flex min-h-0 flex-col gap-3">
              <div
                className={`${browserPanelCollapsed ? 'lg:hidden' : ''} grid h-11 w-full grid-cols-2 overflow-hidden border-2 bg-transparent`}
              >
                <div className="flex min-w-0 items-center justify-between gap-2 px-2.5 py-1">
                  <div className="text-muted-foreground text-[11px]">总数量</div>
                  <div className="text-sm leading-none font-semibold">{stats.total}</div>
                </div>
                <div className="flex min-w-0 items-center justify-between gap-2 border-l px-2.5 py-1">
                  <div className="text-muted-foreground text-[11px]">近7天新增</div>
                  <div className="text-sm leading-none font-semibold text-green-600">
                    {stats.recent_7days}
                  </div>
                </div>
              </div>

              <ChatScopeFilterPanel
                modes={[
                  { label: '聊天', value: 'chat' },
                  { label: '组', value: 'group' },
                  { label: '全部', value: 'all' },
                ]}
                activeMode={browseMode}
                onModeChange={handleBrowseModeChange}
                items={
                  browseMode === 'chat'
                    ? chatList.map((chat) => ({
                        id: chat.chat_id,
                        label: chat.chat_name,
                        title: `${chat.chat_name} (${chat.chat_id})`,
                      }))
                    : browseMode === 'group'
                      ? groups.map((group) => {
                          const memberNames = group.members.map((member) => member.chat_name).join('、')
                          return {
                            id: group.index,
                            label: `${group.name}${group.is_global ? '（全局）' : ''}`,
                            description: memberNames || '暂无已解析聊天',
                            descriptionTitle: memberNames,
                            title: memberNames,
                          }
                        })
                      : []
                }
                selectedItemId={browseMode === 'chat' ? selectedChatId : selectedGroupIndex}
                onItemSelect={(id) => {
                  if (browseMode === 'chat') {
                    handleChatChange(String(id))
                  } else if (browseMode === 'group') {
                    handleGroupChange(Number(id))
                  }
                }}
                emptyContent={
                  <div className="text-muted-foreground px-2 py-6 text-center text-sm">
                    {browseMode === 'group'
                      ? '暂无共享组'
                      : '当前显示全部表达方式'}
                  </div>
                }
                collapsed={browserPanelCollapsed}
                onCollapsedChange={setBrowserPanelCollapsed}
                collapseLabel="折叠浏览列表"
                expandLabel="展开浏览列表"
                footer={
                  <div
                    className="flex w-full items-center justify-between gap-3"
                    title="显示旧格式的表达方式（这些项目会在运行中被转换为新格式）"
                  >
                    <Label htmlFor="show-legacy-expressions" className="cursor-pointer text-sm">
                      显示旧格式
                    </Label>
                    <Switch
                      id="show-legacy-expressions"
                      checked={showLegacyExpressions}
                      onCheckedChange={handleLegacyExpressionsChange}
                    />
                  </div>
                }
                className="lg:flex-1"
                listClassName="max-h-72 w-full space-y-2 p-3 sm:max-h-56 sm:space-y-1 sm:p-2 lg:max-h-none"
              />
            </div>

            <div className="flex min-h-0 flex-col space-y-4 sm:space-y-3">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:gap-2">
                <div className="min-w-0 flex-1">
                  <div className="relative">
                    <Search className="text-muted-foreground absolute top-3 left-3 h-4 w-4 sm:top-2 sm:left-2.5" />
                    <Input
                      id="search"
                      aria-label="搜索"
                      placeholder="搜索情境、风格或上下文..."
                      value={list.searchInput}
                      onChange={(e) => list.setSearchInput(e.target.value)}
                      className="h-10 pl-10 sm:h-8 sm:pl-9"
                    />
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Label htmlFor="page-size" className="text-sm whitespace-nowrap">
                    每页显示
                  </Label>
                  <Select
                    value={list.pageSize.toString()}
                    onValueChange={(value) => list.setPageSize(parseInt(value))}
                  >
                    <SelectTrigger id="page-size" className="h-9 w-20 sm:h-8">
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
              </div>

              {/* 批量操作工具栏 */}
              <div
                className={`${list.selectedCount > 0 ? 'flex' : 'hidden'} mt-4 flex-col items-start justify-between gap-3 border-t pt-4 sm:mt-3 sm:flex-row sm:items-center sm:pt-3`}
              >
                <div className="text-muted-foreground flex items-center gap-2 text-sm">
                  {list.selectedCount > 0 && <span>已选择 {list.selectedCount} 个表达方式</span>}
                </div>
                <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
                  {list.selectedCount > 0 && (
                    <>
                      <Button variant="outline" size="sm" onClick={() => list.clearSelection()}>
                        取消选择
                      </Button>
                      <Button
                        variant="default"
                        size="sm"
                        className="gap-1"
                        onClick={() => batchReviewStatus(Array.from(list.selectedIds), true)}
                      >
                        <Check className="h-4 w-4" />
                        批量通过
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-1"
                        onClick={() => batchReviewStatus(Array.from(list.selectedIds), false)}
                      >
                        <X className="h-4 w-4" />
                        批量不通过
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => setIsBatchDeleteDialogOpen(true)}
                      >
                        <Trash2 className="mr-1 h-4 w-4" />
                        批量删除
                      </Button>
                    </>
                  )}
                </div>
              </div>

              {scopeStatus && (
                <AccentPanel showRetroStripeDivider={false}>
                  <div className="flex flex-wrap items-center gap-2 px-3 py-1.5">
                    {renderStatusIndicator('开启学习', scopeStatus.enableLearning, false)}
                    {renderStatusIndicator('开启使用', scopeStatus.useExpression)}
                    <Button
                      onClick={() => setIsCreateDialogOpen(true)}
                      className="h-7 justify-center gap-1 px-2 text-xs"
                    >
                      <Plus className="h-3.5 w-3.5" />
                      新增
                    </Button>
                    {currentChat && (
                      <div className="grid w-full grid-cols-2 gap-2 sm:ml-auto sm:flex sm:w-auto sm:flex-wrap sm:items-center">
                        <Button
                          variant="outline"
                          size="sm"
                          className="h-7 justify-center gap-1 px-2 text-xs"
                          onClick={exportSelectedExpressionsToFile}
                          disabled={list.selectedCount === 0}
                        >
                          <Download className="h-3.5 w-3.5" />
                          导出所选
                        </Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="outline"
                              size="icon"
                              className="h-7 w-full sm:w-7"
                              title="更多"
                              aria-label="更多操作"
                            >
                              <MoreHorizontal className="h-3.5 w-3.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onSelect={() => importInputRef.current?.click()}>
                              <Upload className="mr-2 h-4 w-4" />
                              导入
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              className="text-destructive focus:text-destructive"
                              onSelect={() => setIsClearConfirmOpen(true)}
                            >
                              <Trash2 className="mr-2 h-4 w-4" />
                              清除
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                        <input
                          ref={importInputRef}
                          type="file"
                          accept="application/json,.json"
                          className="hidden"
                          onChange={handleImportFileChange}
                        />
                      </div>
                    )}
                  </div>
                </AccentPanel>
              )}

              <ExpressionList
                className="lg:min-h-0 lg:flex-1"
                expressions={list.items}
                loading={list.isPending}
                total={list.total}
                page={list.page}
                pageSize={list.pageSize}
                selectedIds={list.selectedIds}
                chatNameMap={chatNameMap}
                hideChatColumn={
                  (browseMode === 'chat' && selectedChatId !== '') ||
                  (browseMode === 'group' && selectedGroupIndex !== null)
                }
                reviewFilter={reviewFilter}
                onReviewFilterChange={(filter) => list.setFilter('reviewFilter', filter)}
                onEdit={handleEdit}
                onViewDetail={handleViewDetail}
                onDelete={(expression) => setDeleteConfirmExpression(expression)}
                onToggleReviewStatus={toggleReviewStatus}
                onToggleSelect={list.toggle}
                onToggleSelectAll={list.toggleAll}
                onPageChange={list.goToPage}
                onJumpToPage={handleJumpToPage}
              />
            </div>
          </div>
        </div>
      </ScrollArea>

      {activeView === 'logs' && (
        <div className="min-h-[38rem] flex-1 pr-4">
          <ExpressionReviewLogPanel onRescued={refreshAll} />
        </div>
      )}

      {activeView === 'quick' && (
        <div className="min-h-0 flex-1 pr-4 pb-2">
          <ExpressionReviewer
            embedded
            open
            mode="quick"
            className="h-full"
            onReviewed={refreshAll}
          />
        </div>
      )}

      {activeView === 'clusters' && (
        <ExpressionClusterBrowser onOpenExpression={handleViewExpressionById} />
      )}

      {/* 详情对话框 */}
      <ExpressionDetailDialog
        expression={selectedExpression}
        open={isDetailDialogOpen}
        onOpenChange={setIsDetailDialogOpen}
        chatNameMap={chatNameMap}
      />

      {/* 创建对话框 */}
      <ExpressionCreateDialog
        open={isCreateDialogOpen}
        onOpenChange={setIsCreateDialogOpen}
        chatList={chatList}
        onSuccess={() => {
          refreshAll()
          setIsCreateDialogOpen(false)
        }}
      />

      {/* 编辑对话框 */}
      <ExpressionEditDialog
        expression={selectedExpression}
        open={isEditDialogOpen}
        onOpenChange={setIsEditDialogOpen}
        chatList={chatList}
        onSuccess={() => {
          refreshAll()
          setIsEditDialogOpen(false)
        }}
      />

      <LegacyExpressionImportDialog
        open={isLegacyImportOpen}
        onOpenChange={setIsLegacyImportOpen}
        chatList={chatList}
        onSuccess={refreshAll}
      />

      {/* 删除确认对话框 */}
      <DeleteConfirmDialog
        expression={deleteConfirmExpression}
        open={!!deleteConfirmExpression}
        onOpenChange={() => setDeleteConfirmExpression(null)}
        onConfirm={handleDelete}
      />

      {/* 批量删除确认对话框 */}
      <BatchDeleteConfirmDialog
        open={isBatchDeleteDialogOpen}
        onOpenChange={setIsBatchDeleteDialogOpen}
        onConfirm={handleBatchDelete}
        count={list.selectedCount}
      />

      <ClearChatExpressionsConfirmDialog
        open={isClearConfirmOpen}
        onOpenChange={setIsClearConfirmOpen}
        chatName={currentChat?.chat_name || ''}
        onConfirm={clearCurrentChat}
      />
    </div>
  )
}
