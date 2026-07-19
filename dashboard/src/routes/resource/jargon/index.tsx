import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, Download, Plus, Search, Trash2, Upload, X } from 'lucide-react'
import { useState } from 'react'

import { ChatScopeFilterPanel } from '@/components/chat-scope-filter-panel'
import { AccentPanel } from '@/components/ui/accent-panel'
import { Button } from '@/components/ui/button'
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
import { Tabs } from '@/components/ui/tabs'
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'
import { formatChatDisplayName } from '@/lib/chat-display'

import {
  batchDeleteJargons,
  batchSetJargonStatus,
  deleteJargon,
  exportJargons,
  getJargonChatList,
  getJargonDetail,
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
}

type JargonStatusFilter = 'confirmed_jargon' | 'confirmed_not_jargon' | 'manual_jargon'
type JargonSummaryTab = 'total' | JargonStatusFilter | 'global_count' | 'complete_count'

/**
 * 黑话管理主页面
 */
export function JargonManagementPage() {
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

  // 黑话列表：分页/搜索/筛选/多选统一由 useDataList 承载，黑话页跨分页与筛选保留选中
  // 搜索防抖内建（searchDebounceMs），不再需要手写防抖 useEffect；
  // 请求竞态由内部 useQuery 处理（queryKey 变化时旧请求结果被丢弃）
  const list = useDataList<Jargon, JargonFilters, number>({
    domain: 'jargon',
    getId: (jargon) => jargon.id,
    initialFilters: { summary: 'total', chatId: 'all' },
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
      const result = await getJargonList({
        page,
        page_size: pageSize,
        search: search || undefined,
        session_id:
          filters.summary !== 'global_count' && filters.chatId !== 'all'
            ? filters.chatId
            : undefined,
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
    }))
  }

  const handleSummaryChange = (value: string) => {
    const summary = value as JargonSummaryTab
    list.updateFilters((filters) => ({
      ...filters,
      summary,
      chatId: summary === 'global_count' ? 'all' : filters.chatId,
    }))
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col p-3 sm:p-4">
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 pr-3">
          {/* 统计标签 */}
          <AccentPanel showRetroStripes={false} className="bg-muted rounded-lg border">
            <Tabs value={summaryFilter} onValueChange={handleSummaryChange}>
              <DashboardTabBar variant="grid" className="grid-cols-2 sm:grid-cols-3 lg:grid-cols-6">
                {[
                  {
                    value: 'total',
                    label: '总数量',
                    count: stats.total,
                    className: 'text-foreground',
                  },
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
                ].map((item) => (
                  <DashboardTabTrigger
                    key={item.value}
                    value={item.value}
                    className="h-8 gap-1.5 px-2 text-xs"
                    aria-label={`${item.label} ${item.count}`}
                  >
                    <span>{item.label}</span>
                    <span className={`leading-none font-semibold ${item.className}`}>
                      {item.count}
                    </span>
                  </DashboardTabTrigger>
                ))}
              </DashboardTabBar>
            </Tabs>
          </AccentPanel>

          {/* 搜索和筛选 */}
          <AccentPanel className="bg-card border" showRetroStripeDivider={false}>
            <div className="p-2.5">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
                <div className="space-y-1">
                  <Label htmlFor="search" className="text-xs">
                    搜索
                  </Label>
                  <div className="relative">
                    <Search className="text-muted-foreground absolute top-2 left-2.5 h-4 w-4" />
                    <Input
                      id="search"
                      placeholder="搜索黑话内容..."
                      value={list.searchInput}
                      onChange={(e) => list.setSearchInput(e.target.value)}
                      className="h-8 pl-9"
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="page-size" className="text-xs">
                    每页显示
                  </Label>
                  <Select
                    value={pageSize.toString()}
                    onValueChange={(value) => list.setPageSize(parseInt(value))}
                  >
                    <SelectTrigger id="page-size" className="h-8">
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
                <div className="flex gap-2">
                  <Button
                    onClick={() => setIsCreateDialogOpen(true)}
                    className="h-8 w-10 px-0"
                    aria-label="新增黑话"
                    title="新增黑话"
                  >
                    <Plus className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    onClick={() => setIsImportDialogOpen(true)}
                    className="h-8 w-10 px-0"
                    aria-label="导入黑话"
                    title="导入黑话"
                  >
                    <Download className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="outline"
                    onClick={openExportDialog}
                    className="h-8 w-10 px-0"
                    aria-label="导出黑话"
                    title="导出黑话"
                  >
                    <Upload className="h-4 w-4" />
                  </Button>
                </div>
              </div>

              {/* 批量操作工具栏 */}
              {selectedIds.size > 0 && (
                <div className="mt-2 flex flex-wrap items-center gap-2 border-t pt-2">
                  <span className="text-muted-foreground text-sm">
                    已选择 {selectedIds.size} 个
                  </span>
                  <Button variant="outline" size="sm" onClick={() => handleBatchSetJargon(true)}>
                    <Check className="mr-1 h-4 w-4" />
                    标记为黑话
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => handleBatchSetJargon(false)}>
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
          </AccentPanel>

          {/* 黑话列表 */}
          <div
            className={`grid grid-cols-1 gap-3 transition-[grid-template-columns] duration-200 lg:h-[calc(100vh-15.5rem)] lg:min-h-[34rem] lg:items-stretch ${
              scopePanelCollapsed
                ? 'lg:grid-cols-[3.25rem_minmax(0,1fr)]'
                : 'lg:grid-cols-[9.5rem_minmax(0,1fr)]'
            }`}
          >
            <ChatScopeFilterPanel
              title="聊天"
              items={[
                { id: 'all', label: '全部聊天' },
                ...chatList.map((chat) => ({
                  id: chat.session_id,
                  label: formatChatDisplayName(chat.chat_name, chat.account_id),
                  title: formatChatDisplayName(chat.chat_name, chat.account_id),
                })),
              ]}
              selectedItemId={filterChatId}
              onItemSelect={(chatId) => handleChatChange(String(chatId))}
              emptyContent={
                <div className="text-muted-foreground px-2 py-6 text-center text-sm">暂无聊天</div>
              }
              collapsed={scopePanelCollapsed}
              onCollapsedChange={setScopePanelCollapsed}
              collapseLabel="折叠范围列表"
              expandLabel="展开范围列表"
              className="[&_[data-chat-scope-panel-header=true]]:px-2 [&_[data-chat-scope-panel-header=true]]:py-1 [&_[data-chat-scope-panel-item=true]]:py-1.5"
              listClassName="space-y-0.5 p-1"
            />

            <div className="min-h-0 lg:h-full">
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
                  hideChatColumn={summaryFilter === 'global_count' || filterChatId !== 'all'}
                  className="lg:h-full"
                  onEdit={handleEdit}
                  onDelete={(jargon) => setDeleteConfirmJargon(jargon)}
                  onToggleSelect={list.toggle}
                  onToggleSelectAll={list.toggleAll}
                  onPageChange={list.goToPage}
                  onJumpToPage={handleJumpToPage}
                />
              )}
            </div>
          </div>
        </div>
      </ScrollArea>

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
