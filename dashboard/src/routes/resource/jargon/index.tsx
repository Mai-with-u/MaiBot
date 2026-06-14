import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, Plus, Search, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import { Button } from '@/components/ui/button'
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
import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'

import {
  batchDeleteJargons,
  batchSetJargonStatus,
  deleteJargon,
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
  JargonEditDialog,
} from './JargonDialogs'
import { JargonList } from './JargonList'

import type { Jargon, JargonChatInfo } from '@/types/jargon'
import type { StatsData } from './types'

interface JargonFilters {
  scope: 'all' | 'global' | 'local'
  chatId: string
  isJargon: string
}

/**
 * 黑话管理主页面
 */
export function JargonManagementPage() {
  const [selectedJargon, setSelectedJargon] = useState<Jargon | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const [deleteConfirmJargon, setDeleteConfirmJargon] = useState<Jargon | null>(null)
  const [isBatchDeleteDialogOpen, setIsBatchDeleteDialogOpen] = useState(false)
  const { toast } = useToast()

  // 黑话列表：分页/搜索/筛选/多选统一由 useDataList 承载，翻页/改参自动重置页码并清空选中
  // 搜索防抖内建（searchDebounceMs），不再需要手写防抖 useEffect；
  // 请求竞态由内部 useQuery 处理（queryKey 变化时旧请求结果被丢弃）
  const list = useDataList<Jargon, JargonFilters, number>({
    domain: 'jargon',
    getId: (jargon) => jargon.id,
    initialFilters: { scope: 'all', chatId: 'all', isJargon: 'all' },
    searchDebounceMs: 300,
    queryFn: async ({ page, pageSize, search, filters }) => {
      const result = await getJargonList({
        page,
        page_size: pageSize,
        search: search || undefined,
        session_id:
          filters.scope !== 'global' && filters.chatId !== 'all' ? filters.chatId : undefined,
        is_jargon:
          filters.isJargon === 'all'
            ? undefined
            : filters.isJargon === 'true'
              ? true
              : filters.isJargon === 'false'
                ? false
                : undefined,
        is_global: filters.scope === 'all' ? undefined : filters.scope === 'global',
      })
      return { items: result.data, total: result.total }
    },
  })
  const jargons = list.items
  const total = list.total
  const loading = list.isPending
  const page = list.page
  const pageSize = list.pageSize
  const scopeFilter = list.filters.scope
  const filterChatId = list.filters.chatId
  const filterIsJargon = list.filters.isJargon
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
    pending: 0,
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

  // 查看详情（事件驱动的读取，失败用 toast 反馈用户动作）
  const handleViewDetail = async (jargon: Jargon) => {
    try {
      const response = await getJargonDetail(jargon.id)
      setSelectedJargon(response.data)
      setIsDetailDialogOpen(true)
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
    setSelectedJargon(jargon)
    setIsEditDialogOpen(true)
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
        description: `已将 ${vars.ids.length} 个词条设为${vars.isJargon ? '黑话' : '非黑话'}`,
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
    list.setFilter('chatId', chatId)
  }

  const handleScopeChange = (scope: 'all' | 'global' | 'local') => {
    list.setFilter('scope', scope)
    if (scope === 'global') {
      list.setFilter('chatId', 'all')
    }
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6 pr-4">

          {/* 统计标签 */}
          <div
            data-dashboard-tabs-list="true"
            className="grid h-10 grid-cols-2 overflow-hidden rounded-lg bg-muted p-1 text-muted-foreground sm:grid-cols-3 lg:grid-cols-6"
          >
            {[
              { label: '总数量', value: stats.total, className: 'text-foreground' },
              { label: '已确认黑话', value: stats.confirmed_jargon, className: 'text-green-600' },
              { label: '确认非黑话', value: stats.confirmed_not_jargon, className: 'text-gray-500' },
              { label: '待判定', value: stats.pending, className: 'text-yellow-600' },
              { label: '全局黑话', value: stats.global_count, className: 'text-blue-600' },
              { label: '推断完成', value: stats.complete_count, className: 'text-purple-600' },
            ].map((item) => (
              <div
                key={item.label}
                data-dashboard-tabs-trigger="true"
                className="inline-flex h-10 min-w-0 items-center justify-center gap-2 px-2 text-sm font-medium whitespace-nowrap transition-all sm:px-3"
              >
                <span className="truncate text-muted-foreground">{item.label}</span>
                <span className={`shrink-0 font-semibold leading-none ${item.className}`}>
                  {item.value}
                </span>
              </div>
            ))}
          </div>

          {/* 搜索和筛选 */}
          <div className="rounded-lg border bg-card p-3">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto] lg:items-end">
              <div className="space-y-1">
                <Label htmlFor="search">搜索</Label>
                <div className="relative">
                  <Search className="absolute left-2.5 top-2 h-4 w-4 text-muted-foreground" />
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
                <Label>状态筛选</Label>
                <Select
                  value={filterIsJargon}
                  onValueChange={(value) => list.setFilter('isJargon', value)}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue placeholder="全部状态" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部状态</SelectItem>
                    <SelectItem value="true">是黑话</SelectItem>
                    <SelectItem value="false">非黑话</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="page-size">每页显示</Label>
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
              <Button onClick={() => setIsCreateDialogOpen(true)} className="h-8 gap-2">
                <Plus className="h-4 w-4" />
                新增
              </Button>
            </div>

            {/* 批量操作工具栏 */}
            {selectedIds.size > 0 && (
              <div className="flex flex-wrap items-center gap-2 mt-4 pt-4 border-t">
                <span className="text-sm text-muted-foreground">已选择 {selectedIds.size} 个</span>
                <Button variant="outline" size="sm" onClick={() => handleBatchSetJargon(true)}>
                  <Check className="h-4 w-4 mr-1" />
                  标记为黑话
                </Button>
                <Button variant="outline" size="sm" onClick={() => handleBatchSetJargon(false)}>
                  <X className="h-4 w-4 mr-1" />
                  标记为非黑话
                </Button>
                <Button variant="outline" size="sm" onClick={() => list.clearSelection()}>
                  取消选择
                </Button>
                <Button variant="destructive" size="sm" onClick={() => setIsBatchDeleteDialogOpen(true)}>
                  <Trash2 className="h-4 w-4 mr-1" />
                  批量删除
                </Button>
              </div>
            )}
          </div>

          {/* 黑话列表 */}
          <div className="grid grid-cols-1 gap-4 lg:h-[calc(100vh-19rem)] lg:min-h-[30rem] lg:grid-cols-[12rem_minmax(0,1fr)] lg:items-stretch">
            <aside className="flex min-h-0 flex-col rounded-lg border bg-card lg:h-full lg:self-stretch lg:overflow-hidden">
              <div className="space-y-2 border-b px-3 py-2">
                <h2 className="text-sm font-medium">范围</h2>
                <div className="grid grid-cols-3 gap-1 rounded-md bg-muted p-1">
                  <button
                    type="button"
                    onClick={() => handleScopeChange('all')}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      scopeFilter === 'all' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    全部
                  </button>
                  <button
                    type="button"
                    onClick={() => handleScopeChange('global')}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      scopeFilter === 'global' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    全局
                  </button>
                  <button
                    type="button"
                    onClick={() => handleScopeChange('local')}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      scopeFilter === 'local' ? 'bg-background shadow-sm' : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    非全局
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">
                {scopeFilter === 'global' ? (
                  <div className="px-2 py-6 text-center text-sm text-muted-foreground">
                    全局黑话不按聊天划分
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => handleChatChange('all')}
                      className={`w-full rounded-md px-2 py-2 text-left text-sm transition-colors ${
                        filterChatId === 'all'
                          ? 'bg-primary text-primary-foreground'
                          : 'text-foreground hover:bg-muted'
                      }`}
                    >
                      全部聊天
                    </button>
                    {chatList.map((chat) => (
                      <button
                        key={chat.session_id}
                        type="button"
                        onClick={() => handleChatChange(chat.session_id)}
                        className={`w-full rounded-md px-2 py-2 text-left text-sm transition-colors ${
                          filterChatId === chat.session_id
                            ? 'bg-primary text-primary-foreground'
                            : 'text-foreground hover:bg-muted'
                        }`}
                        title={chat.chat_name}
                      >
                        <span className="block truncate">{chat.chat_name}</span>
                      </button>
                    ))}
                  </>
                )}
              </div>
            </aside>

            <div className="min-h-0 lg:h-full">
              {list.isError ? (
                <div className="flex h-full min-h-[12rem] flex-col items-center justify-center gap-2 rounded-lg border bg-card py-8">
                  <p className="text-sm text-destructive">{list.error?.message}</p>
                  <Button variant="outline" size="sm" onClick={() => list.refetch()}>
                    重试
                  </Button>
                </div>
              ) : (
                <JargonList
                  jargons={jargons}
                  loading={loading}
                  total={total}
                  page={page}
                  pageSize={pageSize}
                  selectedIds={selectedIds}
                  hideChatColumn={scopeFilter === 'global' || filterChatId !== 'all'}
                  className="lg:h-full"
                  onEdit={handleEdit}
                  onViewDetail={handleViewDetail}
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

      {/* 编辑对话框 */}
      <JargonEditDialog
        jargon={selectedJargon}
        open={isEditDialogOpen}
        onOpenChange={setIsEditDialogOpen}
        chatList={formChatList}
        onSuccess={() => {
          invalidateJargon()
          setIsEditDialogOpen(false)
        }}
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
