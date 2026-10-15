import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Check,
  ChevronDown,
  Download,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { type ReactNode, useState } from 'react'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { MobileAddMenu } from '@/components/layout/MobileAddMenu'
import { MOBILE_GLASS_CLASS } from '@/components/layout/mobile-glass'
import { MobileHeaderActions } from '@/components/layout/MobileHeaderActions'
import { AccentPanel } from '@/components/ui/accent-panel'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'
import { cn } from '@/lib/utils'

import {
  batchDeleteJargons,
  batchSetJargonStatus,
  deleteJargon,
  exportJargons,
  getJargonChatList,
  getJargonDetail,
  getJargonGroups,
  getJargonList,
  getJargonStats,
} from '@/lib/jargon-api'

import {
  BatchDeleteConfirmDialog,
  DeleteConfirmDialog,
  JargonCreateDialog,
  JargonDetailDialog,
  JargonExportDialog,
  JargonImportDialog,
} from './JargonDialogs'
import { JargonList } from './JargonList'

import type { Jargon, JargonChatInfo } from '@/types/jargon'
import type { JargonExportScope } from './JargonDialogs'
import type { StatsData } from './types'

interface JargonFilters {
  summary: JargonSummaryTab
  chatId: string
  browseMode: 'chat' | 'group' | 'all'
  groupIndex: number | null
}

type JargonStatusFilter = 'confirmed_jargon' | 'confirmed_not_jargon' | 'manual_jargon'
type JargonSummaryTab = 'total' | JargonStatusFilter | 'global_count' | 'complete_count'

/**
 * 黑话管理主页面
 */
export function JargonManagementPage({ browserHeader }: { browserHeader?: ReactNode }) {
  const [selectedJargon, setSelectedJargon] = useState<Jargon | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [isImportDialogOpen, setIsImportDialogOpen] = useState(false)
  const [isExportDialogOpen, setIsExportDialogOpen] = useState(false)
  const [exportScope, setExportScope] = useState<JargonExportScope>('all')
  const [exportIncludeChatInfo, setExportIncludeChatInfo] = useState(false)
  const [deleteConfirmJargon, setDeleteConfirmJargon] = useState<Jargon | null>(null)
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [scopePanelCollapsed, setScopePanelCollapsed] = useState(false)
  const { toast } = useToast()

  const groupsQuery = useQuery({
    queryKey: ['jargon', 'groups'],
    queryFn: getJargonGroups,
  })
  const groups = groupsQuery.data?.data ?? []

  // 黑话列表：分页/搜索/筛选/多选统一由 useDataList 承载，黑话页跨分页与筛选保留选中
  // 搜索防抖内建（searchDebounceMs），不再需要手写防抖 useEffect；
  // 请求竞态由内部 useQuery 处理（queryKey 变化时旧请求结果被丢弃）
  const list = useDataList<Jargon, JargonFilters, number>({
    domain: 'jargon',
    getId: (jargon) => jargon.id,
    initialFilters: { summary: 'total', chatId: 'all', browseMode: 'all', groupIndex: null },
    preserveSelectionOnParamsChange: true,
    searchDebounceMs: 300,
    queryFn: async ({ page, pageSize, search, filters }) => {
      const summaryStatus: JargonStatusFilter | undefined = [
        'confirmed_jargon',
        'confirmed_not_jargon',
        'manual_jargon',
      ].includes(filters.summary)
        ? (filters.summary as JargonStatusFilter)
        : undefined
      const group =
        filters.browseMode === 'group'
          ? groups.find((item) => item.index === filters.groupIndex)
          : undefined
      // 未选组或组内尚无真实聊天流时返回空，避免误显示全部词条。
      if (
        filters.browseMode === 'group' &&
        (!group || (!group.is_global && group.session_ids.length === 0))
      ) {
        return { items: [], total: 0 }
      }
      const result = await getJargonList({
        page,
        page_size: pageSize,
        search: search || undefined,
        session_id:
          filters.browseMode === 'chat' &&
          filters.summary !== 'global_count' &&
          filters.chatId !== 'all'
            ? filters.chatId
            : undefined,
        session_ids: group && !group.is_global ? group.session_ids : undefined,
        jargon_status: summaryStatus,
        is_complete: filters.summary === 'complete_count' ? true : undefined,
        is_global: filters.summary === 'global_count' ? true : undefined,
      })
      return { items: result.data, total: result.total }
    },
  })
  const jargons = list.items
  const total = list.total
  const loading = list.isPending || (list.isFetching && jargons.length === 0)
  const page = list.page
  const pageSize = list.pageSize
  const summaryFilter = list.filters.summary
  const filterChatId = list.filters.chatId
  const { browseMode, groupIndex } = list.filters
  const selectedIds = list.selectedIds

  // 统计数据：失败时保持占位数值，不打断页面
  const statsQuery = useQuery({
    queryKey: ['jargon', 'stats'],
    queryFn: getJargonStats,
  })
  const stats: StatsData = statsQuery.data?.data ?? {
    total: 0,
    confirmed_jargon: 0,
    confirmed_not_jargon: 0,
    manual_jargon: 0,
    global_count: 0,
    complete_count: 0,
    chat_count: 0,
    top_chats: {},
  }

  // 聊天列表：侧边栏（仅有记录的聊天）与表单（含空聊天）各取一份
  const chatListQuery = useQuery({
    queryKey: ['jargon', 'chats'],
    queryFn: async () => {
      const [sidebarResponse, formResponse] = await Promise.all([
        getJargonChatList(),
        getJargonChatList({ include_empty: true }),
      ])
      return {
        sidebar: sidebarResponse.data,
        form: formResponse.data,
      }
    },
  })
  const chatList: JargonChatInfo[] = chatListQuery.data?.sidebar ?? []
  const formChatList: JargonChatInfo[] = chatListQuery.data?.form ?? []

  // 任何写操作成功后，按 'jargon' 前缀整体失效（列表 + 统计 + 聊天列表）
  const invalidateJargon = () => list.invalidate()

  const downloadJson = (filename: string, data: unknown) => {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json;charset=utf-8',
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  }

  const handleExport = async (scope: JargonExportScope, includeChatInfo: boolean) => {
    const onlySelected = scope === 'selected'
    const ids = onlySelected ? Array.from(selectedIds) : undefined
    if (onlySelected && selectedIds.size === 0) {
      toast({
        title: '没有选中项目',
        description: '请先选择要导出的黑话',
        variant: 'destructive',
      })
      return
    }

    try {
      setExporting(true)
      const result = await exportJargons({
        ids,
        include_chat_info: includeChatInfo,
      })
      const scope = onlySelected ? 'selected' : 'all'
      const suffix = includeChatInfo ? `${scope}-with-chat` : scope
      downloadJson(`jargons-${suffix}.json`, result)
      toast({
        title: '导出成功',
        description: `已导出 ${result.count} 个黑话`,
      })
      setIsExportDialogOpen(false)
    } catch (error) {
      toast({
        title: '导出失败',
        description: error instanceof Error ? error.message : '无法导出黑话',
        variant: 'destructive',
      })
    } finally {
      setExporting(false)
    }
  }

  const openExportDialog = () => {
    setExportScope(selectedIds.size > 0 ? 'selected' : 'all')
    setExportIncludeChatInfo(false)
    setIsExportDialogOpen(true)
  }

  // 列表行先即时打开详情弹窗，详情字段再按需补齐。
  const handleViewDetail = async (jargon: Jargon) => {
    setSelectedJargon(jargon)
    setIsDetailDialogOpen(true)
    try {
      const response = await getJargonDetail(jargon.id)
      setSelectedJargon((current) => (current?.id === jargon.id ? response.data : current))
    } catch (error) {
      toast({
        title: '加载详情失败',
        description: error instanceof Error ? error.message : '无法加载黑话详情',
        variant: 'destructive',
      })
    }
  }

  // 编辑黑话
  const handleEdit = (jargon: Jargon) => {
    handleViewDetail(jargon)
  }

  // 删除黑话（失败由全局 mutation 错误 toast 呈现）
  const deleteMutation = useMutation({
    mutationFn: (jargon: Jargon) => deleteJargon(jargon.id),
    meta: { errorTitle: '删除失败' },
    onSuccess: (_data, jargon) => {
      toast({
        title: '删除成功',
        description: `已删除黑话: ${jargon.content}`,
      })
      setDeleteConfirmJargon(null)
      invalidateJargon()
    },
  })

  // 删除黑话
  const handleDelete = () => {
    if (!deleteConfirmJargon) return
    deleteMutation.mutate(deleteConfirmJargon)
  }

  // 批量删除（失败由全局 mutation 错误 toast 呈现）
  const batchDeleteMutation = useMutation({
    mutationFn: (ids: number[]) => batchDeleteJargons(ids),
    meta: { errorTitle: '批量删除失败' },
    onSuccess: (_data, ids) => {
      toast({
        title: '批量删除成功',
        description: `已删除 ${ids.length} 个黑话`,
      })
      list.clearSelection()
      setIsBatchDeleteDialogOpen(false)
      invalidateJargon()
    },
  })

  // 批量删除
  const handleBatchDelete = () => {
    batchDeleteMutation.mutate(Array.from(selectedIds))
  }

  // 批量设置为黑话（失败由全局 mutation 错误 toast 呈现）
  const batchSetJargonMutation = useMutation({
    mutationFn: (vars: { ids: number[]; isJargon: boolean }) =>
      batchSetJargonStatus(vars.ids, vars.isJargon),
    meta: { errorTitle: '操作失败' },
    onSuccess: (_data, vars) => {
      toast({
        title: '操作成功',
        description: `已将 ${vars.ids.length} 个词条设为${vars.isJargon ? '黑话' : '无黑话'}`,
      })
      list.clearSelection()
      invalidateJargon()
    },
  })

  // 批量设置为黑话
  const handleBatchSetJargon = (isJargon: boolean) => {
    batchSetJargonMutation.mutate({ ids: Array.from(selectedIds), isJargon })
  }

  // 页面跳转
  const handleJumpToPage = (jumpToPage: string) => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= list.totalPages) {
      list.goToPage(targetPage)
    } else {
      toast({
        title: '无效的页码',
        description: `请输入1-${list.totalPages}之间的页码`,
        variant: 'destructive',
      })
    }
  }

  const handleChatChange = (chatId: string) => {
    list.updateFilters((filters) => ({
      ...filters,
      summary: filters.summary === 'global_count' ? 'total' : filters.summary,
      chatId,
      browseMode: chatId === 'all' ? 'all' : 'chat',
      groupIndex: null,
    }))
  }

  const handleBrowseModeChange = (mode: JargonFilters['browseMode']) => {
    list.updateFilters((filters) => ({
      ...filters,
      browseMode: mode,
      chatId: mode === 'chat' ? filters.chatId : 'all',
      groupIndex: mode === 'group' ? (groups[0]?.index ?? null) : null,
      summary: mode === 'group' && filters.summary === 'global_count' ? 'total' : filters.summary,
    }))
  }

  const handleSummaryChange = (value: string) => {
    const summary = value as JargonSummaryTab
    list.updateFilters((filters) => ({
      ...filters,
      summary,
      chatId: summary === 'global_count' ? 'all' : filters.chatId,
      browseMode: summary === 'global_count' ? 'all' : filters.browseMode,
      groupIndex: summary === 'global_count' ? null : filters.groupIndex,
    }))
  }

  const summaryOptions: Array<{
    value: JargonSummaryTab
    label: string
    count: number
    className: string
  }> = [
    { value: 'total', label: '总数量', count: stats.total, className: 'text-foreground' },
    {
      value: 'confirmed_jargon',
      label: '已确认黑话',
      count: stats.confirmed_jargon,
      className: 'text-green-600',
    },
    {
      value: 'confirmed_not_jargon',
      label: '无黑话',
      count: stats.confirmed_not_jargon,
      className: 'text-gray-500',
    },
    {
      value: 'manual_jargon',
      label: '手动黑话',
      count: stats.manual_jargon,
      className: 'text-amber-600',
    },
    {
      value: 'global_count',
      label: '全局黑话',
      count: stats.global_count,
      className: 'text-blue-600',
    },
    {
      value: 'complete_count',
      label: '推断完成',
      count: stats.complete_count,
      className: 'text-purple-600',
    },
  ]
  const activeSummary =
    summaryOptions.find((option) => option.value === summaryFilter) ?? summaryOptions[0]

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden p-4 pb-6 sm:p-6">
      {/* 手机页头右上角：导出与添加 */}
      <MobileHeaderActions>
        <button
          type="button"
          aria-label="导出黑话"
          onClick={openExportDialog}
          data-mobile-header-icon-button="true"
          className={cn(
            'flex h-11 w-11 items-center justify-center rounded-full',
            MOBILE_GLASS_CLASS
          )}
        >
          <Upload className="h-5 w-5" strokeWidth={1.7} />
        </button>
        <MobileAddMenu
          label="添加黑话"
          items={[
            { label: '新增黑话', onSelect: () => setIsCreateDialogOpen(true) },
            { label: '导入黑话', onSelect: () => setIsImportDialogOpen(true) },
          ]}
        />
      </MobileHeaderActions>
      <div className="min-h-0 flex-1 overflow-y-auto lg:overflow-hidden">
        <div className="pr-3 pb-2 sm:pr-4 lg:h-full">
          <div
            className={`grid grid-cols-1 items-stretch gap-5 transition-[grid-template-columns] duration-200 sm:gap-4 lg:h-full lg:min-h-[30rem] ${
              scopePanelCollapsed
                ? browserHeader
                  ? 'lg:grid-cols-[8rem_minmax(0,1fr)]'
                  : 'lg:grid-cols-[3.25rem_minmax(0,1fr)]'
                : 'lg:grid-cols-[13.5rem_minmax(0,1fr)]'
            }`}
          >
            <div className="hidden min-h-0 flex-col gap-3 lg:flex">
              {browserHeader}
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
                        id: chat.session_id,
                        label: formatChatDisplayName(chat.chat_name, chat.account_id),
                        title: formatChatDisplayName(chat.chat_name, chat.account_id),
                      }))
                    : browseMode === 'group'
                      ? groups.map((group) => ({
                          id: group.index,
                          label: `${group.name}${group.is_global ? '（全局）' : ''}`,
                          description:
                            group.members
                              .map((chat) => formatChatDisplayName(chat.chat_name, chat.account_id))
                              .join('、') || '暂无已解析聊天',
                          title: group.members
                            .map((chat) => formatChatDisplayName(chat.chat_name, chat.account_id))
                            .join('、'),
                        }))
                      : []
                }
                selectedItemId={browseMode === 'chat' ? filterChatId : groupIndex}
                onItemSelect={(id) => {
                  if (browseMode === 'group') {
                    list.setFilter('groupIndex', Number(id))
                  } else {
                    handleChatChange(String(id))
                  }
                }}
                emptyContent={
                  <div className="text-muted-foreground px-2 py-6 text-center text-sm">
                    {browseMode === 'all'
                      ? '当前显示全部黑话'
                      : browseMode === 'group'
                        ? groupsQuery.isError
                          ? '共享组加载失败'
                          : groupsQuery.isPending
                            ? '正在加载共享组...'
                            : '暂无共享组'
                        : '暂无聊天'}
                  </div>
                }
                collapsed={scopePanelCollapsed}
                onCollapsedChange={setScopePanelCollapsed}
                collapseLabel="折叠范围列表"
                expandLabel="展开范围列表"
                className="flex-1"
                listClassName="w-full space-y-2 p-3 sm:space-y-1 sm:p-2"
              />
            </div>
            <div className="flex min-h-0 flex-col gap-3">
              <div className="lg:hidden">{browserHeader}</div>
              {/* 搜索和筛选 */}
              <div className="shrink-0 border-2 bg-transparent">
                <div className="px-3 py-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <div className="hidden shrink-0 lg:block">
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="outline"
                            className="h-8 justify-between gap-3 bg-transparent px-2.5 text-xs"
                            aria-label={`黑话分类：${activeSummary.label} ${activeSummary.count}`}
                          >
                            <span>{activeSummary.label}</span>
                            <span className="ml-auto flex items-center gap-2">
                              <span className={`font-semibold ${activeSummary.className}`}>
                                {activeSummary.count}
                              </span>
                              <ChevronDown className="h-3.5 w-3.5" />
                            </span>
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="min-w-52">
                          <DropdownMenuRadioGroup
                            value={summaryFilter}
                            onValueChange={handleSummaryChange}
                          >
                            {summaryOptions.map((option) => (
                              <DropdownMenuRadioItem
                                key={option.value}
                                value={option.value}
                                aria-label={`${option.label} ${option.count}`}
                              >
                                <span>{option.label}</span>
                                <span className={`ml-auto pl-4 font-semibold ${option.className}`}>
                                  {option.count}
                                </span>
                              </DropdownMenuRadioItem>
                            ))}
                          </DropdownMenuRadioGroup>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>

                    <div className="w-full min-w-0 sm:min-w-48 sm:flex-1">
                      <div className="relative">
                        <Search className="text-muted-foreground absolute top-2 left-2.5 h-4 w-4" />
                        <input
                          id="search"
                          aria-label="搜索黑话"
                          placeholder="搜索黑话内容..."
                          value={list.searchInput}
                          onChange={(e) => list.setSearchInput(e.target.value)}
                          className="border-input focus-visible:ring-ring text-foreground placeholder:text-muted-foreground h-8 w-full border-2 bg-transparent py-1 pr-3 pl-9 text-sm focus-visible:ring-1 focus-visible:outline-none"
                        />
                      </div>
                    </div>

                    <div className="flex items-center gap-2 max-lg:hidden">
                      <Button
                        onClick={() => setIsCreateDialogOpen(true)}
                        className="h-7 gap-1 px-2 text-xs"
                        aria-label="新增黑话"
                      >
                        <Plus className="h-3.5 w-3.5" />
                        新增
                      </Button>
                      <Button
                        variant="outline"
                        onClick={openExportDialog}
                        className="h-7 gap-1 px-2 text-xs"
                        aria-label="导出黑话"
                      >
                        <Upload className="h-3.5 w-3.5" />
                        导出
                      </Button>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="outline"
                            size="icon"
                            className="h-7 w-7"
                            aria-label="更多操作"
                            title="更多操作"
                          >
                            <MoreHorizontal className="h-3.5 w-3.5" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onSelect={() => setIsImportDialogOpen(true)}>
                            <Download className="mr-2 h-4 w-4" />
                            导入黑话
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>

                  {/* 批量操作工具栏 */}
                  {selectedIds.size > 0 && (
                    <div className="mt-2 flex flex-wrap items-center gap-2 border-t pt-2">
                      <span className="text-muted-foreground text-sm">
                        已选择 {selectedIds.size} 个
                      </span>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleBatchSetJargon(true)}
                      >
                        <Check className="mr-1 h-4 w-4" />
                        标记为黑话
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleBatchSetJargon(false)}
                      >
                        <X className="mr-1 h-4 w-4" />
                        标记为无黑话
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => list.clearSelection()}>
                        取消选择
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => setIsBatchDeleteDialogOpen(true)}
                      >
                        <Trash2 className="mr-1 h-4 w-4" />
                        批量删除
                      </Button>
                    </div>
                  )}
                </div>
              </div>

              {/* 手机：分类改为可点按的统计卡，聊天范围改为横滑胶囊 */}
              <div
                data-mobile-jargon-summary="true"
                className="bg-card grid grid-cols-4 rounded-3xl py-3 lg:hidden"
              >
                {summaryOptions.slice(0, 4).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={summaryFilter === option.value}
                    onClick={() => handleSummaryChange(option.value)}
                    className={cn(
                      'flex min-w-0 flex-col items-center gap-0.5 px-1',
                      summaryFilter !== option.value && 'opacity-60'
                    )}
                  >
                    <b className="text-xl font-semibold tabular-nums">{option.count}</b>
                    <span className="text-muted-foreground truncate text-xs">{option.label}</span>
                  </button>
                ))}
              </div>
              <div className="flex gap-2 lg:hidden" role="group" aria-label="浏览范围">
                {(
                  [
                    { value: 'chat', label: '聊天' },
                    { value: 'group', label: '组' },
                    { value: 'all', label: '全部' },
                  ] as const
                ).map((mode) => (
                  <Button
                    key={mode.value}
                    variant={browseMode === mode.value ? 'default' : 'outline'}
                    size="sm"
                    aria-pressed={browseMode === mode.value}
                    onClick={() => handleBrowseModeChange(mode.value)}
                  >
                    {mode.label}
                  </Button>
                ))}
              </div>
              {browseMode === 'group' && (
                <div className="flex gap-2 overflow-x-auto lg:hidden">
                  {groups.map((group) => (
                    <Button
                      key={group.index}
                      variant={groupIndex === group.index ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => list.setFilter('groupIndex', group.index)}
                    >
                      {group.name}
                    </Button>
                  ))}
                  {groupsQuery.isError ? (
                    <span>共享组加载失败</span>
                  ) : (
                    !groupsQuery.isPending && groups.length === 0 && <span>暂无共享组</span>
                  )}
                </div>
              )}
              <nav
                aria-label="聊天范围"
                data-mobile-pill-nav="true"
                className={cn(
                  '-mx-4 flex gap-2 overflow-x-auto px-4 [scrollbar-width:none] lg:hidden',
                  browseMode !== 'chat' && 'hidden'
                )}
              >
                {[
                  { id: 'all', label: '全部聊天' },
                  ...chatList.map((chat) => ({
                    id: chat.session_id,
                    label: formatChatDisplayName(chat.chat_name, chat.account_id),
                  })),
                ].map((item) => {
                  const active = filterChatId === item.id
                  return (
                    <button
                      key={item.id}
                      type="button"
                      aria-pressed={active}
                      data-active={active ? 'true' : 'false'}
                      onClick={() => handleChatChange(String(item.id))}
                      className={cn(
                        'h-9 shrink-0 rounded-full px-4 text-[15px]',
                        active ? 'bg-foreground text-background font-semibold' : 'bg-card'
                      )}
                    >
                      {item.label}
                    </button>
                  )
                })}
              </nav>

              {/* 黑话列表 */}
              <div className="min-h-0 lg:flex-1">
                {list.isError ? (
                  <AccentPanel className="bg-card h-full min-h-[12rem] border">
                    <div className="flex h-full min-h-[12rem] flex-col items-center justify-center gap-2 py-8">
                      <p className="text-destructive text-sm">{list.error?.message}</p>
                      <Button variant="outline" size="sm" onClick={() => list.refetch()}>
                        重试
                      </Button>
                    </div>
                  </AccentPanel>
                ) : (
                  <JargonList
                    jargons={jargons}
                    loading={loading}
                    total={total}
                    page={page}
                    pageSize={pageSize}
                    selectedIds={selectedIds}
                    hideChatColumn={
                      summaryFilter === 'global_count' ||
                      (browseMode === 'chat' && filterChatId !== 'all')
                    }
                    className="lg:h-full"
                    onEdit={handleEdit}
                    onDelete={(jargon) => setDeleteConfirmJargon(jargon)}
                    onToggleSelect={list.toggle}
                    onToggleSelectAll={list.toggleAll}
                    onPageChange={list.goToPage}
                    onJumpToPage={handleJumpToPage}
                    onPageSizeChange={list.setPageSize}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* 详情对话框 */}
      <JargonDetailDialog
        jargon={selectedJargon}
        open={isDetailDialogOpen}
        onOpenChange={setIsDetailDialogOpen}
        chatList={formChatList}
        onChanged={(jargon) => {
          setSelectedJargon(jargon)
          invalidateJargon()
        }}
      />

      {/* 创建对话框 */}
      <JargonCreateDialog
        open={isCreateDialogOpen}
        onOpenChange={setIsCreateDialogOpen}
        chatList={formChatList}
        onSuccess={() => {
          invalidateJargon()
          setIsCreateDialogOpen(false)
        }}
      />

      {/* 导入对话框 */}
      <JargonImportDialog
        open={isImportDialogOpen}
        onOpenChange={setIsImportDialogOpen}
        chatList={formChatList}
        onSuccess={() => {
          list.clearSelection()
          invalidateJargon()
        }}
      />

      {/* 导出对话框 */}
      <JargonExportDialog
        open={isExportDialogOpen}
        onOpenChange={setIsExportDialogOpen}
        selectedCount={selectedIds.size}
        scope={exportScope}
        includeChatInfo={exportIncludeChatInfo}
        exporting={exporting}
        onScopeChange={setExportScope}
        onIncludeChatInfoChange={setExportIncludeChatInfo}
        onExport={handleExport}
      />

      {/* 删除确认对话框 */}
      <DeleteConfirmDialog
        jargon={deleteConfirmJargon}
        open={!!deleteConfirmJargon}
        onOpenChange={() => setDeleteConfirmJargon(null)}
        onConfirm={handleDelete}
      />

      {/* 批量删除确认对话框 */}
      <BatchDeleteConfirmDialog
        open={isBatchDeleteDialogOpen}
        onOpenChange={setIsBatchDeleteDialogOpen}
        onConfirm={handleBatchDelete}
        count={selectedIds.size}
      />
    </div>
  )
}
