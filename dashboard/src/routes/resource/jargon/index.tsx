import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, Settings, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { Ref } from 'react'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { LearningCreateDialog } from '@/components/learning-create-dialog'
import { LearningExportDialog } from '@/components/learning-export-dialog'
import { ActionBar } from '@/components/ui/action-bar'
import { Button } from '@/components/ui/button'
import {
  DataBrowserHeader,
  DataBrowserLayout,
  DataToolbar,
  MetricStrip,
} from '@/components/ui/data-browser'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { PageFrame } from '@/components/ui/surface'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName, formatChatTargetLabel } from '@/lib/chat-display'
import { cn } from '@/lib/utils'

import {
  batchDeleteJargons,
  batchSetJargonStatus,
  deleteJargon,
  exportJargons,
  getJargonChatList,
  getJargonDetail,
  getJargonVersions,
  getJargonGroups,
  getJargonList,
  getJargonOverview,
  getJargonStats,
} from '@/lib/jargon-api'

import {
  BatchDeleteConfirmDialog,
  DeleteConfirmDialog,
  JargonCreateDialog,
  JargonDetailDialog,
} from './JargonDialogs'
import { JargonList } from './JargonList'

import type { Jargon, JargonChatInfo } from '@/types/jargon'
import type { StatsData } from './types'
import type { LearningBrowserTabs, LearningCreateHandle } from '../learning'

interface JargonFilters {
  sortBy: 'time' | 'time_asc' | 'created' | 'created_asc' | 'count'
  summary: JargonSummaryTab
  chatId: string
  browseMode: 'chat' | 'group'
  groupIndex: string | null
}

type JargonStatusFilter = 'confirmed_jargon' | 'confirmed_not_jargon' | 'pending' | 'manual_jargon'
type JargonSummaryTab = JargonStatusFilter | 'global_count' | 'complete_count'

/**
 * 黑话管理主页面
 */
export function JargonManagementPage({
  browserTabs,
  createHandle,
}: {
  browserTabs?: LearningBrowserTabs
  createHandle?: Ref<LearningCreateHandle>
}) {
  const queryClient = useQueryClient()
  const detailRequest = useRef(0)
  const [selectedJargon, setSelectedJargon] = useState<Jargon | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
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
  const [isExportDialogOpen, setIsExportDialogOpen] = useState(false)
  const [deleteConfirmJargon, setDeleteConfirmJargon] = useState<Jargon | null>(null)
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
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
    initialFilters: {
      summary: 'confirmed_jargon',
      chatId: 'all',
      browseMode: 'chat',
      groupIndex: null,
      sortBy: 'time',
    },
    preserveSelectionOnParamsChange: true,
    searchDebounceMs: 300,
    queryFn: async ({ page, pageSize, search, filters }) => {
      const summaryStatus: JargonStatusFilter | undefined = [
        'confirmed_jargon',
        'confirmed_not_jargon',
        'pending',
        'manual_jargon',
      ].includes(filters.summary)
        ? (filters.summary as JargonStatusFilter)
        : undefined
      if (filters.browseMode === 'group' && !filters.groupIndex) {
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
        grouped: true,
        compact: true,
        group_id: filters.browseMode === 'group' ? filters.groupIndex ?? undefined : undefined,
        jargon_status: summaryStatus,
        sort_by: filters.sortBy,
        is_complete: filters.summary === 'complete_count' ? true : undefined,
        is_global: filters.summary === 'global_count' ? true : undefined,
      })
      return { items: result.data, total: result.total }
    },
  })
  const historySearch = useMutation({
    mutationFn: (content: string) => getJargonVersions(content),
    onSuccess: rows => {
      const saved = rows.find(row => row.archived)
      if (!saved) { toast({ title: '未找到同名历史版本' }); return }
      setSelectedJargon(saved)
      setIsDetailDialogOpen(true)
    },
    meta: { errorTitle: '读取历史版本失败' },
  })
  const jargons = list.items
  const detailIndex = selectedJargon
    ? jargons.findIndex((row) => row.id === selectedJargon.id || row.scope_ids?.includes(selectedJargon.id) || row.content === selectedJargon.content)
    : -1
  const total = list.total
  const loading = list.isPending || (list.isFetching && jargons.length === 0)
  const page = list.page
  const pageSize = list.pageSize
  const summaryFilter = list.filters.summary
  const filterChatId = list.filters.chatId
  const { browseMode, groupIndex } = list.filters
  const firstGroupId = groups[0]?.id ?? null
  const selectedGroupExists = groups.some((group) => group.id === groupIndex)
  const setFilter = list.setFilter
  useEffect(() => {
    if (browseMode === 'group' && groupsQuery.isSuccess && !selectedGroupExists && groupIndex !== firstGroupId) {
      setFilter('groupIndex', firstGroupId)
    }
  }, [browseMode, groupsQuery.isSuccess, selectedGroupExists, groupIndex, firstGroupId, setFilter])

  const selectedIds = list.selectedIds
  const scopeSelections = useRef(new Map<number, number[]>())
  const selectedVersions = new Set([...selectedIds].flatMap(id => scopeSelections.current.get(id) ?? [id]))
  const rememberScopes = (row: Jargon) => scopeSelections.current.set(row.id, row.scope_ids ?? [row.id])

  // 删除末页最后一项后同步实际查询页，避免分页显示与返回的数据错位。
  if (!list.isPending && !list.isFetching && !list.isError && list.page > list.totalPages) {
    list.goToPage(list.totalPages)
  }

  // 统计数据：失败时保持占位数值，不打断页面
  const [summaryOpen, setSummaryOpen] = useState(false)
  const overview = useQuery({ queryKey: ['jargon', 'overview'], queryFn: getJargonOverview })
  const statsQuery = useQuery({
    queryKey: ['jargon', 'stats'],
    queryFn: () => getJargonStats(false),
    enabled: summaryOpen,
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
      const { data } = await getJargonChatList({ include_empty: true })
      return { sidebar: data.filter(chat => chat.jargon_count), form: data }
    },
  })
  const chatList: JargonChatInfo[] = chatListQuery.data?.sidebar ?? []
  const formChatList: JargonChatInfo[] = chatListQuery.data?.form ?? []

  // 任何写操作成功后，按 'jargon' 前缀整体失效（列表 + 统计 + 聊天列表）
  const invalidateJargon = () => list.invalidate()

  // 展开的作用域行直接打开对应释义。
  const handleViewDetail = async (jargon: Jargon) => {
    detailRequest.current++
    setSelectedJargon(jargon)
    setIsDetailDialogOpen(true)
    if (jargon.scope_kind) return
    try {
      const response = await getJargonDetail(jargon.id)
      setSelectedJargon((current) => (current?.id === jargon.id ? { ...current, ...response.data } : current))
    } catch (error) {
      toast({
        title: '加载详情失败',
        description: error instanceof Error ? error.message : '无法加载黑话详情',
        variant: 'destructive',
      })
    }
  }

  const openTerm = async (row: Jargon) => {
    if (!row.scope_kind || row.is_global) return handleViewDetail(row)
    const request = ++detailRequest.current
    try {
      const versions = await queryClient.fetchQuery({
        queryKey: ['jargon', 'versions', row.content, false],
        queryFn: () => getJargonVersions(row.content, false),
      })
      if (request === detailRequest.current) {
        await handleViewDetail(versions.find(version => version.scope_kind === 'global') ?? row)
      }
    } catch (error) {
      if (request === detailRequest.current) toast({ title: '加载详情失败', description: String(error), variant: 'destructive' })
    }
  }

  // 删除黑话（失败由全局 mutation 错误 toast 呈现）
  const deleteMutation = useMutation({
    mutationFn: (jargon: Jargon) => jargon.scope_ids?.length ? batchDeleteJargons(jargon.scope_ids) : deleteJargon(jargon.id),
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
    batchDeleteMutation.mutate(Array.from(selectedVersions))
  }

  // 批量设置为黑话（失败由全局 mutation 错误 toast 呈现）
  const batchSetJargonMutation = useMutation({
    mutationFn: (vars: { ids: number[]; isJargon: boolean }) =>
      batchSetJargonStatus(vars.ids, vars.isJargon),
    meta: { errorTitle: '操作失败' },
    onSuccess: (_data, vars) => {
      toast({
        title: '操作成功',
        description: `已将 ${vars.ids.length} 个词条设为${vars.isJargon ? '黑话' : '不是黑话'}`,
      })
      list.clearSelection()
      invalidateJargon()
    },
  })

  // 批量设置为黑话
  const handleBatchSetJargon = (isJargon: boolean) => {
    batchSetJargonMutation.mutate({ ids: Array.from(selectedVersions), isJargon })
  }

  const handleChatChange = (chatId: string) => {
    list.updateFilters((filters) => ({
      ...filters,
      summary: chatId !== 'all' && filters.summary === 'global_count' ? 'confirmed_jargon' : filters.summary,
      chatId,
      browseMode: 'chat',
      groupIndex: null,
    }))
  }

  const handleBrowseModeChange = (mode: JargonFilters['browseMode']) => {
    if (mode === browseMode) return
    list.updateFilters((filters) => ({
      ...filters,
      browseMode: mode,
      chatId: mode === 'chat' ? filters.chatId : 'all',
      groupIndex: mode === 'group' ? firstGroupId : null,
      summary: mode === 'group' && filters.summary === 'global_count' ? 'confirmed_jargon' : filters.summary,
    }))
  }

  const handleSummaryChange = (value: string) => {
    const summary = value as JargonSummaryTab
    list.updateFilters((filters) => ({
      ...filters,
      summary,
      chatId: summary === 'global_count' ? 'all' : filters.chatId,
      browseMode: summary === 'global_count' ? 'chat' : filters.browseMode,
      groupIndex: summary === 'global_count' ? null : filters.groupIndex,
    }))
  }

  const summaryOptions: Array<{
    value: JargonSummaryTab
    label: string
    count: number
    className: string
  }> = [
    {
      value: 'confirmed_jargon',
      label: '黑话',
      count: stats.confirmed_jargon,
      className: 'text-green-600',
    },
    {
      value: 'confirmed_not_jargon',
      label: '不是黑话',
      count: stats.confirmed_not_jargon,
      className: 'text-gray-500',
    },
    {
      value: 'manual_jargon',
      label: '已暂停更新',
      count: stats.manual_jargon,
      className: 'text-amber-600',
    },
    {
      value: 'global_count',
      label: '通用词条',
      count: stats.global_count,
      className: 'text-blue-600',
    },
    {
      value: 'pending',
      label: '积累中',
      count: stats.pending ?? 0,
      className: 'text-amber-600',
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
  const activeCount = statsQuery.data ? activeSummary.count
    : summaryFilter === 'confirmed_jargon' || summaryFilter === 'complete_count' ? overview.data?.[summaryFilter] : undefined

  const toolbar = (
    <DataToolbar
      search={list.searchInput}
      onSearchChange={list.setSearchInput}
      placeholder="搜索黑话内容..."
      actions={
        <>
          <Select
            value={list.filters.sortBy}
            onValueChange={(value: JargonFilters['sortBy']) => list.setFilter('sortBy', value)}
          >
            <SelectTrigger controlSize="sm" className="w-[130px]" aria-label="黑话排序">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="time">最近活跃</SelectItem>
              <SelectItem value="time_asc">最早活跃</SelectItem>
              <SelectItem value="created">最新创建</SelectItem>
              <SelectItem value="created_asc">最早创建</SelectItem>
              <SelectItem value="count">遇见最多</SelectItem>
            </SelectContent>
          </Select>
          <DropdownMenu onOpenChange={setSummaryOpen}>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="group gap-1.5"
                aria-label={`黑话分类：${activeSummary.label} ${activeSummary.count}`}
              >
                <span>{activeSummary.label}</span>
                <span className={cn('tabular-nums', activeSummary.className)}>
                  {activeCount?.toLocaleString() ?? '…'}
                </span>
                <ChevronDown
                  strokeWidth={1.75}
                  className="size-3.5 shrink-0 text-muted-foreground/75 transition-transform duration-150 group-data-[state=open]:rotate-180"
                  aria-hidden="true"
                />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="min-w-52">
              <DropdownMenuRadioGroup value={summaryFilter} onValueChange={handleSummaryChange}>
                {summaryOptions.map((option) => (
                  <DropdownMenuRadioItem
                    key={option.value}
                    value={option.value}
                    aria-label={`${option.label} ${option.count}`}
                  >
                    <span>{option.label}</span>
                    <span className={cn('ml-auto pl-4 tabular-nums', option.className)}>
                      {statsQuery.data ? option.count.toLocaleString() : '…'}
                    </span>
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            variant="outline"
            size="icon-sm"
            onClick={() => setIsExportDialogOpen(true)}
            aria-label="导出黑话"
            title="导出黑话"
          >
            <Upload className="h-4 w-4" />
          </Button>
        </>
      }
    />
  )

  const summary = (
    <MetricStrip
      items={[
        { label: '黑话', value: overview.data?.confirmed_jargon.toLocaleString() ?? '…', positive: true },
        { label: '推断完成', value: overview.data?.complete_count.toLocaleString() ?? '…' },
      ]}
    />
  )

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden p-3 pb-4 sm:p-4">
      {isExportDialogOpen && (
        <LearningExportDialog
          kind="jargon"
          selectedIds={selectedVersions}
          chats={chatList.map((chat) => ({
            id: chat.session_id,
            name: formatChatDisplayName(chat.chat_name, chat.account_id),
            targetId: chat.target_id,
          }))}
          groups={groups.map((group) => ({
            id: group.id,
            name: group.name,
            chatIds: group.session_ids,
            isGlobal: group.is_global,
            details: [
              group.name,
              group.is_global ? '所有聊天在此组共同学习黑话' : `包含 ${group.members.length} 个聊天`,
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
          onExport={({ ids, chatIds, groupId, includeChatInfo }) =>
            exportJargons({ ids, session_ids: groupId ? undefined : chatIds, group_id: groupId, include_chat_info: includeChatInfo })
          }
        />
      )}
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto lg:overflow-hidden">
        {!browserTabs && <DataBrowserHeader title="学习" summary={summary} />}
        <DataBrowserLayout
          tabs={browserTabs?.(summary)}
          sidebar={
            <ChatScopeFilterPanel
              density="compact"
              title="聊天范围"
              titleActions={
                browseMode === 'group' && (
                  <Button asChild variant="ghost" size="icon-xs">
                    <a
                      href={groupIndex ? `/chat-management?kind=jargon&group=${encodeURIComponent(groupIndex)}` : '/chat-management?kind=jargon'}
                      title="管理相通组"
                      aria-label="管理相通组"
                    >
                      <Settings className="h-3 w-3" />
                    </a>
                  </Button>
                )
              }
              titleMeta={
                browseMode === 'chat' ? `${chatList.length} 个` : `${groups.length} 组`
              }
              modes={[
                { label: '聊天', value: 'chat' },
                { label: '相通', value: 'group' },
              ]}
              activeMode={browseMode}
              onModeChange={handleBrowseModeChange}
              items={
                browseMode === 'chat'
                  ? [
                      { id: 'all', label: '全部', title: '全部聊天', meta: overview.data?.total.toLocaleString() },
                      ...chatList.map((chat) => ({
                        id: chat.session_id,
                        label: chat.chat_name,
                        meta: chat.jargon_count?.toLocaleString(),
                        description: formatChatTargetLabel(chat),
                        descriptionTitle: formatChatTargetLabel(chat),
                        title: formatChatDisplayName(chat.chat_name, chat.account_id),
                      })),
                    ]
                  : groups.map((group) => {
                      const memberNames = group.members
                        .map((chat) => formatChatDisplayName(chat.chat_name, chat.account_id))
                        .join('、')
                      return {
                        id: group.id,
                        meta: group.jargon_count.toLocaleString(),
                        label: `${group.name}${group.is_global ? '（所有聊天）' : ''}`,
                        description: memberNames || '暂无已解析聊天',
                        descriptionTitle: memberNames,
                        title: memberNames,
                      }
                    })
              }
              selectedItemId={browseMode === 'chat' ? filterChatId : groupIndex}
              onItemSelect={(id) => {
                if (browseMode === 'chat') handleChatChange(String(id))
                else {
                  list.updateFilters((filters) => ({
                    ...filters,
                    browseMode: 'group',
                    groupIndex: String(id),
                    summary: filters.summary === 'global_count' ? 'confirmed_jargon' : filters.summary,
                  }))
                }
              }}
              emptyContent={
                <div className="text-muted-foreground px-2 py-6 text-center text-sm">
                  {groupsQuery.isError ? '相通组加载失败' : groupsQuery.isPending ? '正在加载相通组...' : '暂无相通组'}
                </div>
              }
              footer={browseMode === 'chat' && (chatListQuery.isPending || chatListQuery.isError) && (
                <span className="text-muted-foreground text-xs">
                  {chatListQuery.isError ? '聊天列表加载失败' : '正在加载聊天...'}
                </span>
              )}
              collapsed={scopePanelCollapsed}
              onCollapsedChange={setScopePanelCollapsed}
              collapseLabel="折叠范围列表"
              expandLabel="展开范围列表"
              listClassName="max-h-64 lg:max-h-none"
            />
          }
        >
          {list.isError ? (
            <PageFrame className="flex min-h-0 flex-col">
              {toolbar}
              <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-2 p-8">
                <p className="text-destructive text-sm">{list.error?.message}</p>
                <Button variant="outline" size="sm" onClick={() => list.refetch()}>
                  重试
                </Button>
              </div>
            </PageFrame>
          ) : (
            <JargonList
              className="min-h-0"
              toolbar={toolbar}
              emptyAction={list.searchInput.trim() && <Button size="xs" variant="outline"
                disabled={historySearch.isPending} onClick={() => historySearch.mutate(list.searchInput.trim())}>
                查看同名历史版本
              </Button>}
              jargons={jargons}
              loading={loading}
              total={total}
              page={page}
              pageSize={pageSize}
              selectedIds={selectedIds}
              hideChatColumn={summaryFilter === 'global_count'}
              onEdit={handleViewDetail}
              onOpenTerm={openTerm}
              onDelete={(jargon) => setDeleteConfirmJargon(jargon)}
              onToggleSelect={id => { const row = jargons.find(item => item.id === id); if (row) rememberScopes(row); list.toggle(id) }}
              onToggleSelectAll={() => { jargons.forEach(rememberScopes); list.toggleAll() }}
              onPageChange={list.goToPage}
              onPageSizeChange={list.setPageSize}
            />
          )}
        </DataBrowserLayout>
      </div>

      {/* 详情对话框 */}
      <JargonDetailDialog
        jargon={selectedJargon}
        open={isDetailDialogOpen}
        onOpenChange={(open) => { detailRequest.current++; setIsDetailDialogOpen(open) }}
        chatList={formChatList}
        groups={groups}
        pageNavigation={detailIndex >= 0 ? {
          index: detailIndex, count: jargons.length,
          go: (offset) => { const row = jargons[detailIndex + offset]; if (row) void openTerm(row) },
        } : undefined}
        onScopeChange={(value) => {
          setSelectedJargon(value)
          if (!value) setIsDetailDialogOpen(false)
        }}
        onChanged={(jargon) => {
          setSelectedJargon(jargon)
          invalidateJargon()
        }}
      />

      {/* 创建对话框 */}
      <LearningCreateDialog
        open={isCreateChoiceOpen}
        onOpenChange={setIsCreateChoiceOpen}
        kind="jargon"
        initialTargetIds={browseMode === 'chat' && filterChatId !== 'all' ? [filterChatId] : []}
        initialMode="auto"
        onManual={() => setIsCreateDialogOpen(true)}
        onSuccess={(isGlobal) => {
          if (isGlobal) handleSummaryChange('global_count')
          invalidateJargon()
        }}
      />
      <JargonCreateDialog
        groups={groups}
        open={isCreateDialogOpen}
        onOpenChange={setIsCreateDialogOpen}
        chatList={formChatList}
        onSuccess={(isGlobal) => {
          if (isGlobal) handleSummaryChange('global_count')
          invalidateJargon()
          setIsCreateDialogOpen(false)
        }}
      />

      {/* 删除确认对话框 */}
      <DeleteConfirmDialog
        jargon={deleteConfirmJargon}
        open={!!deleteConfirmJargon}
        onOpenChange={() => setDeleteConfirmJargon(null)}
        onConfirm={handleDelete}
      />

      {/* 跨页选择保留，使用悬浮操作弹条避免生硬顶动正文列表。 */}
      <ActionBar
        open={selectedIds.size > 0}
        count={selectedIds.size}
        countLabel="个黑话"
        onClear={list.clearSelection}
      >
        <Button variant="outline" size="xs" onClick={() => handleBatchSetJargon(true)}>
          <Check className="h-3 w-3" />
          标记为黑话
        </Button>
        <Button variant="outline" size="xs" onClick={() => handleBatchSetJargon(false)}>
          <X className="h-3 w-3" />
          标记为不是黑话
        </Button>
        <Button variant="destructive" size="xs" onClick={() => setIsBatchDeleteDialogOpen(true)}>
          <Trash2 className="h-3 w-3" />
          批量删除
        </Button>
      </ActionBar>

      {/* 批量删除确认对话框 */}
      <BatchDeleteConfirmDialog
        open={isBatchDeleteDialogOpen}
        onOpenChange={setIsBatchDeleteDialogOpen}
        onConfirm={handleBatchDelete}
        count={selectedVersions.size}
      />
    </div>
  )
}
