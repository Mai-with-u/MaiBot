import { Settings } from 'lucide-react'
import {
  ArrowLeft,
  FileClock,
  MoreHorizontal,
  Network,
  Star,
  StarOff,
  Trash2,
  Upload,
  X,
  Zap,
} from 'lucide-react'
import { useImperativeHandle, useState } from 'react'
import type { Ref } from 'react'

import { useQuery } from '@tanstack/react-query'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { LearningExportDialog } from '@/components/learning-export-dialog'
import { LearningCreateDialog } from '@/components/learning-create-dialog'
import { Button } from '@/components/ui/button'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
} from '@/components/ui/data-browser'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ExpressionReviewer } from '@/components/expression-reviewer'
import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { MobileHeaderActions } from '@/components/layout/MobileHeaderActions'
import { useHideMobileTabBar } from '@/components/layout/mobile-nav-context'
import { useMediaQuery } from '@/hooks/use-media-query'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { SwitchField } from '@/components/ui/switch'
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName, formatChatTargetLabel } from '@/lib/chat-display'
import { cn } from '@/lib/utils'

import {
  batchDeleteExpressions,
  deleteExpression,
  exportExpressions,
  getChatList,
  getExpressionDetail,
  getExpressionChatTargets,
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
  ExpressionEditDialog,
} from './ExpressionDialogs'
import { ExpressionClusterBrowser } from './ExpressionClusterBrowser'
import { ExpressionList } from './ExpressionList'
import { ExpressionReviewLogPanel } from './ExpressionReviewLogPanel'
import { ExpressionScopeStatus } from './ExpressionScopeStatus'

import type { ChatInfo, Expression, ExpressionGroupInfo } from '@/types/expression'
import type { StatsData } from './types'
import type { LearningBrowserTabs, LearningCreateHandle } from '../learning'

type IndicatorStatus = 'on' | 'off' | 'mixed'
type ExpressionReviewFilter = 'all' | 'user_checked' | 'unchecked'
type BrowseMode = 'chat' | 'group' | 'all'

/** useDataList 的筛选袋：浏览维度 + 审核筛选 */
interface ExpressionFilters {
  browseMode: BrowseMode
  selectedChatId: string
  selectedGroupIndex: string | null
  sortBy: 'time' | 'time_asc' | 'created' | 'created_asc'
  reviewFilter: ExpressionReviewFilter
}

const INITIAL_FILTERS: ExpressionFilters = {
  browseMode: 'chat',
  selectedChatId: '',
  selectedGroupIndex: null,
  reviewFilter: 'all',
  sortBy: 'time',
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
export function ExpressionManagementPage({
  browserTabs,
  createHandle,
}: {
  browserTabs?: LearningBrowserTabs
  createHandle?: Ref<LearningCreateHandle>
}) {
  const { toast } = useToast()

  // 浏览面板与对话框等纯 UI 局部态
  const [browserPanelCollapsed, setBrowserPanelCollapsed] = useState(false)
  const [selectedExpression, setSelectedExpression] = useState<Expression | null>(null)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isCreateChoiceOpen, setIsCreateChoiceOpen] = useState(false)
  // 新增入口在学习页的「添加学习内容」里，对话框仍在这里打开。
  useImperativeHandle(
    createHandle,
    () => ({
      open: (mode) =>
        mode === 'manual' ? setIsCreateDialogOpen(true) : setIsCreateChoiceOpen(true),
    }),
    []
  )
  const [deleteConfirmExpression, setDeleteConfirmExpression] = useState<Expression | null>(null)
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
  const [isClearConfirmOpen, setIsClearConfirmOpen] = useState(false)
  const [isExportDialogOpen, setIsExportDialogOpen] = useState(false)
  const [isMoreActionsOpen, setIsMoreActionsOpen] = useState(false)
  // 手机上默认进入卡片审核，与设计稿一致；桌面端默认列表
  const isMobileViewport = useMediaQuery('(max-width: 1023.98px)')
  const [activeView, setActiveView] = useState<'list' | 'logs' | 'quick' | 'clusters'>(() =>
    window.matchMedia('(max-width: 1023.98px)').matches ? 'quick' : 'list'
  )

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
          ? groups.find((group) => group.id === filters.selectedGroupIndex)
          : undefined
      // 已选中的非全局组但无任何聊天：直接返回空，避免无意义请求
      if (
        filters.browseMode === 'group' &&
        (!selectedGroup || (!selectedGroup.is_global && selectedGroup.chat_ids.length === 0))
      ) {
        return { items: [], total: 0 }
      }
      const response = await getExpressionList({
        page,
        page_size: pageSize,
        search: search || undefined,
        chat_id: filters.browseMode === 'chat' ? filters.selectedChatId || undefined : undefined,
        chat_ids: selectedGroup && !selectedGroup.is_global ? selectedGroup.chat_ids : undefined,
        include_legacy: true,
        review_filter: filters.reviewFilter,
        sort_by: filters.sortBy,
      })
      return { items: response.data, total: response.total }
    },
  })

  const filters = list.filters
  const { browseMode, selectedChatId, selectedGroupIndex, reviewFilter } = filters

  // 统计：失败时保持占位数值，不打断页面；与列表同领域，list.invalidate() 会一并失效
  const statsQuery = useQuery({
    queryKey: ['expression', 'stats', { include_legacy: true }],
    queryFn: () => getExpressionStats({ include_legacy: true }),
  })
  const stats: StatsData = statsQuery.data ?? DEFAULT_STATS

  // 审核统计：uncheckedCount 由此派生
  const reviewStatsQuery = useQuery({
    queryKey: ['expression', 'review-stats'],
    queryFn: () => getReviewStats(),
  })
  const uncheckedCount = reviewStatsQuery.data?.unchecked ?? 0

  // 聊天流列表包含历史及非当前账号的数据，由条目标签区分。
  const chatListQuery = useQuery({
    queryKey: ['expression', 'chats', { include_legacy: true }],
    queryFn: () => getChatList({ include_legacy: true }),
  })
  const chatList: ChatInfo[] = chatListQuery.data ?? []
  const chatTargetsQuery = useQuery({
    queryKey: ['expression', 'chat-targets'],
    queryFn: () => getExpressionChatTargets(),
    enabled: isCreateDialogOpen,
  })
  const chatNameMap = new Map<string, string>()
  chatList.forEach((chat) => chatNameMap.set(chat.chat_id, chat.chat_name))

  // 表达共享组与聊天列表保持相同的完整数据范围。
  const groupsQuery = useQuery({
    queryKey: ['expression', 'groups', { include_legacy: true }],
    queryFn: () => getExpressionGroups({ include_legacy: true }),
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

  // 删除末页最后一项后同步实际查询页，避免分页数字与空列表指向不同的页。
  if (!list.isPending && !list.isFetching && !list.isError && list.page > list.totalPages) {
    list.goToPage(list.totalPages)
  }

  // 写成功后清空选中再刷新，筛选后已消失的记录不能残留在批量操作里。
  const refreshAll = () => {
    list.clearSelection()
    list.invalidate()
  }

  // 审核（单条 / 批量）：写成功后 invalidate 重新拉取（服务端为准）
  const { toggleReviewStatus, batchReviewStatus } = useExpressionReview({ onChanged: refreshAll })

  // 当前选中的具体聊天（用于导入清除与作用域指示）
  const currentChat =
    browseMode === 'chat' && selectedChatId
      ? (chatList.find((chat) => chat.chat_id === selectedChatId) ?? null)
      : null

  // 清除当前聊天：写成功后 invalidate 刷新（导入统一走学习页的「导入学习内容」）
  const { clearCurrentChat } = useExpressionImportExport({
    currentChat,
    onChanged: refreshAll,
    onClearSelection: list.clearSelection,
    onCloseClearConfirm: () => setIsClearConfirmOpen(false),
  })

  // 手机上卡片审核是全屏的滑卡界面，底部按钮占据标签栏的位置
  useHideMobileTabBar(isMobileViewport && activeView === 'quick')

  // 顶部视图切换（表达 / 快速审核 / AI审核记录）
  const handleActiveViewChange = (view: 'list' | 'logs' | 'quick' | 'clusters') => {
    setActiveView(view)
    if (view === 'list') {
      list.refetch()
      statsQuery.refetch()
    }
    reviewStatsQuery.refetch()
  }

  // 聚类项只有 ID，先加载完整记录后复用编辑对话框展示与修改。
  const handleOpenExpressionById = async (expressionId: number) => {
    try {
      const result = await getExpressionDetail(expressionId)
      setSelectedExpression(result)
      setIsEditDialogOpen(true)
    } catch (error) {
      toast({
        title: '加载表达方式失败',
        description: error instanceof Error ? error.message : '无法加载表达方式',
        variant: 'destructive',
      })
    }
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

  // 浏览范围切换：按聊天 / 相通（含特殊的全部组）（变更后 setFilter 自动重置页码并清空选中）
  const handleBrowseModeChange = (mode: BrowseMode) => {
    list.setFilter('browseMode', mode)
    if (mode === 'chat' && !selectedChatId && chatList.length > 0) {
      list.setFilter('selectedChatId', chatList[0].chat_id)
    }
    if (mode !== 'chat') {
      list.setFilter('selectedChatId', '')
    }
    if (mode === 'group' && selectedGroupIndex === null && groups.length > 0) {
      list.setFilter('selectedGroupIndex', groups[0].id)
    }
    if (mode !== 'group') {
      list.setFilter('selectedGroupIndex', null)
    }
  }

  const handleChatChange = (chatId: string) => {
    list.setFilter('selectedChatId', chatId)
  }

  const handleGroupChange = (groupIndex: string | null) => {
    list.setFilter('selectedGroupIndex', groupIndex)
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

  const summary = (
    <MetricStrip
      responsiveCompact
      items={[
        { label: '总数量', value: stats.total.toLocaleString() },
        { label: '近7天新增', value: `+${stats.recent_7days.toLocaleString()}`, positive: true },
      ]}
    />
  )

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden p-3 pb-4 sm:p-4">
      {isExportDialogOpen && (
        <LearningExportDialog
          kind="expression"
          selectedIds={list.selectedIds}
          chats={chatList.map((chat) => ({
            id: chat.chat_id,
            name: formatChatDisplayName(chat.chat_name, chat.account_id),
            targetId: chat.target_id,
          }))}
          groups={groups.map((group) => ({
            id: group.id,
            name: group.name,
            chatIds: group.chat_ids,
            isGlobal: group.is_global,
            details: [
              group.name,
              group.is_global ? '全局共享组：包含全部聊天' : `包含 ${group.members.length} 个聊天`,
              ...group.members.map((member) =>
                [
                  formatChatDisplayName(member.chat_name, member.account_id),
                  formatChatTargetLabel(member),
                ]
                  .filter(Boolean)
                  .join(' · ')
              ),
            ].join('\n'),
          }))}
          loading={chatListQuery.isPending || groupsQuery.isPending}
          error={chatListQuery.isError || groupsQuery.isError}
          onRetry={() => {
            void chatListQuery.refetch()
            void groupsQuery.refetch()
          }}
          onBack={() => setIsExportDialogOpen(false)}
          onExport={({ ids, chatIds }) => exportExpressions({ ids, chat_ids: chatIds })}
        />
      )}
      {activeView !== 'list' && (
        <div className="mb-4 flex shrink-0 justify-end gap-2 sm:mb-3">
          {/* 手机上由页头的「列表模式」返回，这里不再重复 */}
          <Button
            variant="outline"
            onClick={() => handleActiveViewChange('list')}
            className="h-10 justify-center gap-2 max-lg:hidden sm:h-9"
          >
            <ArrowLeft className="h-4 w-4" />
            返回表达
          </Button>
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
        </div>
      )}

      <div
        className={
          activeView === 'list'
            ? 'flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto lg:overflow-hidden'
            : 'hidden'
        }
      >
        {!browserTabs && <DataBrowserHeader title="学习" summary={summary} />}
        <DataBrowserLayout
          tabs={browserTabs?.(summary)}
          sidebar={
            <ChatScopeFilterPanel
              density="compact"
              title="聊天范围"
              titleActions={
                browseMode !== 'chat' && (
                  <Button asChild variant="ghost" size="icon-xs">
                    <a
                      href={`/chat-management?kind=expression&group=${browseMode === 'all' ? 'all' : (selectedGroupIndex ?? 'all')}`}
                      title="管理当前相通组"
                      aria-label="管理当前相通组"
                    >
                      <Settings className="h-3 w-3" />
                    </a>
                  </Button>
                )
              }
              titleMeta={
                browseMode === 'chat' ? `${chatList.length} 个` : `${groups.length + 1} 组`
              }
              modes={[
                { label: '聊天', value: 'chat' },
                { label: '相通', value: 'group' },
              ]}
              activeMode={browseMode === 'chat' ? 'chat' : 'group'}
              onModeChange={(mode) => handleBrowseModeChange(mode === 'group' ? 'all' : 'chat')}
              items={
                browseMode === 'chat'
                  ? chatList.map((chat) => ({
                      id: chat.chat_id,
                      label: (
                        <span className="flex min-w-0 items-center gap-1">
                          <span className="truncate">{chat.chat_name}</span>
                          {(chat.is_legacy || chat.is_other_account) && (
                            <span className="shrink-0 rounded border px-1 text-[10px] font-normal">
                              {chat.is_legacy ? '旧格式' : '其他账号'}
                            </span>
                          )}
                        </span>
                      ),
                      description: formatChatTargetLabel(chat),
                      descriptionTitle: formatChatTargetLabel(chat),
                      meta: chat.expression_count?.toLocaleString(),
                      title: formatChatDisplayName(chat.chat_name, chat.account_id),
                      trailing: (
                        <ExpressionScopeStatus
                          enableLearning={chat.enable_learning ? 'on' : 'off'}
                          useExpression={chat.use_expression ? 'on' : 'off'}
                        />
                      ),
                    }))
                  : [
                      {
                        id: 'all',
                        label: '全部',
                        description: '包含全部聊天',
                        title: '全部聊天的相通组',
                        meta: statsQuery.data?.total.toLocaleString(),
                        trailing:
                          chatList.length > 0 ? (
                            <ExpressionScopeStatus
                              enableLearning={summarizeStatus(
                                chatList.map((chat) => chat.enable_learning)
                              )}
                              useExpression={summarizeStatus(
                                chatList.map((chat) => chat.use_expression)
                              )}
                            />
                          ) : undefined,
                      },
                      ...groups.map((group) => {
                        const memberNames = group.members
                          .map((member) =>
                            formatChatDisplayName(member.chat_name, member.account_id)
                          )
                          .join('、')
                        const groupChatIds = new Set(group.chat_ids)
                        const expressionCount = group.is_global
                          ? statsQuery.data?.total
                          : chatListQuery.isSuccess
                            ? chatList
                                .filter((chat) => groupChatIds.has(chat.chat_id))
                                .reduce((total, chat) => total + (chat.expression_count ?? 0), 0)
                            : undefined
                        return {
                          id: group.id,
                          meta: expressionCount?.toLocaleString(),
                          label: `${group.name}${group.is_global ? '（全局）' : ''}`,
                          description: memberNames || '暂无已解析聊天',
                          descriptionTitle: memberNames,
                          title: memberNames,
                          trailing:
                            group.members.length > 0 ? (
                              <ExpressionScopeStatus
                                enableLearning={summarizeStatus(
                                  group.members.map((member) => member.enable_learning)
                                )}
                                useExpression={summarizeStatus(
                                  group.members.map((member) => member.use_expression)
                                )}
                              />
                            ) : undefined,
                        }
                      }),
                    ]
              }
              selectedItemId={
                browseMode === 'chat'
                  ? selectedChatId
                  : browseMode === 'all'
                    ? 'all'
                    : selectedGroupIndex
              }
              onItemSelect={(id) => {
                if (browseMode === 'chat') handleChatChange(String(id))
                else if (id === 'all') handleBrowseModeChange('all')
                else {
                  handleBrowseModeChange('group')
                  handleGroupChange(String(id))
                }
              }}
              emptyContent={
                <div className="text-muted-foreground px-2 py-6 text-center text-sm">暂无聊天</div>
              }
              collapsed={browserPanelCollapsed}
              onCollapsedChange={setBrowserPanelCollapsed}
              collapseLabel="折叠浏览列表"
              expandLabel="展开浏览列表"
              listClassName="max-h-64 lg:max-h-none"
            />
          }
        >
          <ExpressionList
            className="min-h-0"
            toolbar={
              <DataToolbar
                search={list.searchInput}
                onSearchChange={list.setSearchInput}
                placeholder="搜索情境、风格或上下文..."
                actions={
                  <>
                    <Select
                      value={filters.sortBy}
                      onValueChange={(value: ExpressionFilters['sortBy']) =>
                        list.setFilter('sortBy', value)
                      }
                    >
                      <SelectTrigger
                        controlSize="sm"
                        className="w-[130px]"
                        aria-label="表达方式排序"
                      >
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="time">最近活跃</SelectItem>
                        <SelectItem value="time_asc">最早活跃</SelectItem>
                        <SelectItem value="created">最新创建</SelectItem>
                        <SelectItem value="created_asc">最早创建</SelectItem>
                      </SelectContent>
                    </Select>
                    <SwitchField
                      controlSize="sm"
                      label="仅精选"
                      checked={reviewFilter === 'user_checked'}
                      onCheckedChange={(checked) =>
                        list.setFilter('reviewFilter', checked ? 'user_checked' : 'all')
                      }
                    />
                    <Button
                      variant="outline"
                      size="icon-sm"
                      onClick={() => setIsExportDialogOpen(true)}
                      title="导出表达方式"
                      aria-label="导出表达方式"
                    >
                      <Upload className="h-4 w-4" />
                    </Button>
                    <DropdownMenu open={isMoreActionsOpen} onOpenChange={setIsMoreActionsOpen}>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="outline"
                          size="icon-sm"
                          className="data-[state=open]:bg-accent shrink-0"
                          title="更多"
                          aria-label={isMoreActionsOpen ? '关闭更多操作' : '更多操作'}
                        >
                          <span className="relative h-3.5 w-3.5" aria-hidden="true">
                            <MoreHorizontal
                              className={`absolute inset-0 h-3.5 w-3.5 transition-all duration-200 ease-out ${
                                isMoreActionsOpen
                                  ? 'scale-0 rotate-90 opacity-0'
                                  : 'scale-100 rotate-0 opacity-100'
                              }`}
                            />
                            <X
                              className={`absolute inset-0 h-3.5 w-3.5 transition-all duration-200 ease-out ${
                                isMoreActionsOpen
                                  ? 'scale-100 rotate-0 opacity-100'
                                  : 'scale-0 -rotate-90 opacity-0'
                              }`}
                            />
                          </span>
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="expression-actions-menu-content">
                        <DropdownMenuItem
                          onSelect={() =>
                            list.setFilter(
                              'reviewFilter',
                              reviewFilter === 'unchecked' ? 'all' : 'unchecked'
                            )
                          }
                        >
                          <StarOff className="mr-2 h-4 w-4" />
                          {reviewFilter === 'unchecked' ? '显示全部' : '仅显示未精选'}
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => handleActiveViewChange('quick')}>
                          <Zap className="mr-2 h-4 w-4" />
                          精选
                          {uncheckedCount > 0 && (
                            <span className="ml-auto rounded-full bg-orange-500 px-1.5 py-0.5 text-xs leading-none text-white">
                              {uncheckedCount > 99 ? '99+' : uncheckedCount}
                            </span>
                          )}
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => handleActiveViewChange('clusters')}>
                          <Network className="mr-2 h-4 w-4" />
                          聚类
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          disabled={!currentChat}
                          onSelect={() => setIsClearConfirmOpen(true)}
                        >
                          <Trash2 className="mr-2 h-4 w-4" />
                          清除
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </>
                }
              >
                {/* 多选后的操作留在窗口工具栏里，不挤出第二个外框。 */}
                {list.selectedCount > 0 && (
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
                    <span className="text-muted-foreground mr-auto text-xs">
                      已选择 {list.selectedCount} 个表达方式
                    </span>
                    <Button variant="outline" size="xs" onClick={list.clearSelection}>
                      取消选择
                    </Button>
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => batchReviewStatus(Array.from(list.selectedIds), true)}
                    >
                      <Star className="h-3 w-3" />
                      批量精选
                    </Button>
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => batchReviewStatus(Array.from(list.selectedIds), false)}
                    >
                      <StarOff className="h-3 w-3" />
                      取消精选
                    </Button>
                    <Button
                      variant="destructive"
                      size="xs"
                      onClick={() => setIsBatchDeleteDialogOpen(true)}
                    >
                      <Trash2 className="h-3 w-3" />
                      批量删除
                    </Button>
                  </div>
                )}
              </DataToolbar>
            }
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
            onDelete={(expression) => setDeleteConfirmExpression(expression)}
            onToggleReviewStatus={toggleReviewStatus}
            onToggleSelect={list.toggle}
            onToggleSelectAll={list.toggleAll}
            onPageChange={list.goToPage}
            onPageSizeChange={list.setPageSize}
          />
        </DataBrowserLayout>
      </div>

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

      {/* 手机页头右上角：在卡片审核与列表之间切换 */}
      <MobileHeaderActions>
        <button
          type="button"
          onClick={() => handleActiveViewChange(activeView === 'quick' ? 'list' : 'quick')}
          data-mobile-header-text-button="true"
          className={cn('h-11 rounded-full px-4 text-[15px]', MOBILE_GLASS_CLASS)}
        >
          {activeView === 'quick' ? '列表模式' : '卡片审核'}
        </button>
      </MobileHeaderActions>

      {activeView === 'clusters' && (
        <ExpressionClusterBrowser onOpenExpression={handleOpenExpressionById} />
      )}

      {/* 创建对话框 */}
      <LearningCreateDialog
        open={isCreateChoiceOpen}
        onOpenChange={setIsCreateChoiceOpen}
        kind="expression"
        initialTargetIds={currentChat ? [currentChat.chat_id] : []}
        initialMode="auto"
        onManual={() => setIsCreateDialogOpen(true)}
        onSuccess={(isGlobal) => {
          if (isGlobal) handleBrowseModeChange('all')
          refreshAll()
        }}
      />
      <ExpressionCreateDialog
        open={isCreateDialogOpen}
        onOpenChange={setIsCreateDialogOpen}
        chatList={chatTargetsQuery.data ?? []}
        onSuccess={(isGlobal) => {
          if (isGlobal) handleBrowseModeChange('all')
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
