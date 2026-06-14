import { useState, useEffect, useMemo } from 'react'

import { useMutation, useQuery } from '@tanstack/react-query'
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  Edit,
  Eye,
  Hash,
  Search,
  Trash2,
  User,
  Users,
} from 'lucide-react'
import { Clock, MessageSquare } from 'lucide-react'

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
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'

import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'

import {
  batchDeletePersons,
  deletePerson,
  getPersonDetail,
  getPersonList,
  getPersonStats,
  updatePerson,
} from '@/lib/person-api'
import { cn } from '@/lib/utils'

import type { PersonInfo, PersonUpdateRequest } from '@/types/person'

interface PersonFilters {
  known?: boolean
  platform?: string
}

export function PersonManagementPage() {
  const [selectedPerson, setSelectedPerson] = useState<PersonInfo | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [deleteConfirmPerson, setDeleteConfirmPerson] = useState<PersonInfo | null>(null)
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [jumpToPage, setJumpToPage] = useState('')
  const { toast } = useToast()

  // 人物列表：分页/搜索/筛选/多选统一由 useDataList 承载，翻页/改参自动重置页码并清空选中
  const list = useDataList<PersonInfo, PersonFilters, string>({
    domain: 'persons',
    getId: (person) => person.person_id,
    initialFilters: { known: undefined, platform: undefined },
    queryFn: async ({ page, pageSize, search, filters }) => {
      const result = await getPersonList({
        page,
        page_size: pageSize,
        search: search || undefined,
        is_known: filters.known,
        platform: filters.platform,
      })
      return { items: result.data, total: result.total }
    },
  })
  const persons = list.items
  const total = list.total
  const loading = list.isPending
  const page = list.page
  const pageSize = list.pageSize

  // 统计卡片：失败时保持占位数值，不打断页面；与列表同领域，list.invalidate() 会一并失效
  const statsQuery = useQuery({
    queryKey: ['persons', 'stats'],
    queryFn: getPersonStats,
  })
  const stats =
    statsQuery.data ?? { total: 0, known: 0, unknown: 0, platforms: {} as Record<string, number> }

  // 查看详情（事件驱动的读取，失败用 toast 反馈用户动作）
  const handleViewDetail = async (person: PersonInfo) => {
    try {
      const detail = await getPersonDetail(person.person_id)
      setSelectedPerson(detail)
      setIsDetailDialogOpen(true)
    } catch (error) {
      toast({
        title: '加载详情失败',
        description: error instanceof Error ? error.message : '无法加载人物详情',
        variant: 'destructive',
      })
    }
  }

  // 编辑人物
  const handleEdit = (person: PersonInfo) => {
    setSelectedPerson(person)
    setIsEditDialogOpen(true)
  }

  // 删除人物（失败由全局 mutation 错误 toast 呈现）
  const deleteMutation = useMutation({
    mutationFn: (person: PersonInfo) => deletePerson(person.person_id),
    meta: { errorTitle: '删除失败' },
    onSuccess: (_data, person) => {
      toast({
        title: '删除成功',
        description: `已删除人物信息: ${person.person_name || person.nickname || person.user_id}`,
      })
      setDeleteConfirmPerson(null)
      list.invalidate()
    },
  })

  // 获取平台列表
  const platforms = useMemo(() => {
    return Object.keys(stats.platforms)
  }, [stats.platforms])

  // 打开批量删除对话框
  const openBatchDeleteDialog = () => {
    if (list.selectedCount === 0) {
      toast({
        title: '未选择任何人物',
        description: '请先选择要删除的人物',
        variant: 'destructive',
      })
      return
    }
    setBatchDeleteDialogOpen(true)
  }

  // 批量删除（失败由全局 mutation 错误 toast 呈现）
  const batchDeleteMutation = useMutation({
    mutationFn: (personIds: string[]) => batchDeletePersons(personIds),
    meta: { errorTitle: '批量删除失败' },
    onSuccess: (data) => {
      toast({
        title: '批量删除完成',
        description: data.message,
      })
      list.clearSelection()
      setBatchDeleteDialogOpen(false)
      list.invalidate()
    },
  })

  // 页面跳转
  const handleJumpToPage = () => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= list.totalPages) {
      list.goToPage(targetPage)
      setJumpToPage('')
    } else {
      toast({
        title: '无效的页码',
        description: `请输入1-${list.totalPages}之间的页码`,
        variant: 'destructive',
      })
    }
  }

  // 格式化时间
  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col p-4 sm:p-6">
      {/* 页面标题 */}
      <div className="mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold flex items-center gap-2">
              <Users className="h-8 w-8" strokeWidth={2} />
              人物信息管理
            </h1>
            <p className="text-muted-foreground mt-1 text-sm sm:text-base">
              管理麦麦认识的所有人物信息
            </p>
          </div>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="space-y-4 sm:space-y-6 pr-4">

      {/* 统计卡片 */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm text-muted-foreground">总人数</div>
          <div className="text-2xl font-bold mt-1">{stats.total}</div>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm text-muted-foreground">已认识</div>
          <div className="text-2xl font-bold mt-1 text-green-600">{stats.known}</div>
        </div>
        <div className="rounded-lg border bg-card p-4">
          <div className="text-sm text-muted-foreground">未认识</div>
          <div className="text-2xl font-bold mt-1 text-muted-foreground">{stats.unknown}</div>
        </div>
      </div>

      {/* 搜索和过滤 */}
      <div className="rounded-lg border bg-card p-4">
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <div className="sm:col-span-2">
            <Label htmlFor="search">搜索</Label>
            <div className="relative mt-1.5">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                id="search"
                placeholder="搜索名称、昵称或用户ID..."
                value={list.searchInput}
                onChange={(e) => list.setSearchInput(e.target.value)}
                className="pl-9"
              />
            </div>
          </div>
          <div>
            <Label htmlFor="filter-known">认识状态</Label>
            <Select
              value={list.filters.known === undefined ? 'all' : String(list.filters.known)}
              onValueChange={(value) =>
                list.setFilter('known', value === 'all' ? undefined : value === 'true')
              }
            >
              <SelectTrigger id="filter-known" className="mt-1.5">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="true">已认识</SelectItem>
                <SelectItem value="false">未认识</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label htmlFor="filter-platform">平台</Label>
            <Select
              value={list.filters.platform || 'all'}
              onValueChange={(value) =>
                list.setFilter('platform', value === 'all' ? undefined : value)
              }
            >
              <SelectTrigger id="filter-platform" className="mt-1.5">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部平台</SelectItem>
                {platforms.map((platform) => (
                  <SelectItem key={platform} value={platform}>
                    {platform} ({stats.platforms[platform]})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* 批量操作工具栏 */}
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mt-4 pt-4 border-t">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            {list.selectedCount > 0 && (
              <span>已选择 {list.selectedCount} 个人物</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="page-size" className="text-sm whitespace-nowrap">每页显示</Label>
            <Select
              value={pageSize.toString()}
              onValueChange={(value) => list.setPageSize(parseInt(value))}
            >
              <SelectTrigger id="page-size" className="w-20">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
            {list.selectedCount > 0 && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => list.clearSelection()}
                >
                  取消选择
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={openBatchDeleteDialog}
                >
                  <Trash2 className="h-4 w-4 mr-1" />
                  批量删除
                </Button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* 人物列表 */}
      <div className="rounded-lg border bg-card">
        {/* 桌面端表格视图 */}
        <div className="hidden md:block">
          <Table aria-label="人物信息列表">
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">
                  <Checkbox
                    checked={persons.length > 0 && list.selectedCount === persons.length}
                    onCheckedChange={list.toggleAll}
                    aria-label="全选"
                  />
                </TableHead>
                <TableHead>状态</TableHead>
                <TableHead>名称</TableHead>
                <TableHead>昵称</TableHead>
                <TableHead>平台</TableHead>
                <TableHead>用户ID</TableHead>
                <TableHead>最后更新</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                    <ThinkingIllustration size="sm" className="mx-auto" />
                  </TableCell>
                </TableRow>
              ) : list.isError ? (
                <TableRow>
                  <TableCell colSpan={8} className="text-center py-8">
                    <div className="space-y-2">
                      <p className="text-sm text-destructive">{list.error?.message}</p>
                      <Button variant="outline" size="sm" onClick={() => list.refetch()}>
                        重试
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ) : persons.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="text-center py-8 text-muted-foreground">
                    暂无数据
                  </TableCell>
                </TableRow>
              ) : (
                persons.map((person) => (
                  <TableRow key={person.id}>
                    <TableCell>
                      <Checkbox
                        checked={list.isSelected(person.person_id)}
                        onCheckedChange={() => list.toggle(person.person_id)}
                        aria-label={`选择 ${person.person_name || person.nickname || person.user_id}`}
                      />
                    </TableCell>
                    <TableCell>
                      <div className={cn(
                        'inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium',
                        person.is_known
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                          : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400'
                      )}>
                        {person.is_known ? '已认识' : '未认识'}
                      </div>
                    </TableCell>
                    <TableCell className="font-medium">
                      {person.person_name || <span className="text-muted-foreground">-</span>}
                    </TableCell>
                    <TableCell>{person.nickname || '-'}</TableCell>
                    <TableCell>{person.platform}</TableCell>
                    <TableCell className="font-mono text-sm">{person.user_id}</TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatTime(person.last_know)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-2">
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => handleViewDetail(person)}
                        >
                          <Eye className="h-4 w-4 mr-1" />
                          详情
                        </Button>
                        <Button
                          variant="default"
                          size="sm"
                          onClick={() => handleEdit(person)}
                        >
                          <Edit className="h-4 w-4 mr-1" />
                          编辑
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => setDeleteConfirmPerson(person)}
                          className="bg-red-600 hover:bg-red-700 text-white"
                        >
                          <Trash2 className="h-4 w-4 mr-1" />
                          删除
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {/* 移动端卡片视图 */}
        <div className="md:hidden space-y-3 p-4">
          {loading ? (
            <div className="text-center py-8 text-muted-foreground">
              <ThinkingIllustration size="sm" className="mx-auto" />
            </div>
          ) : list.isError ? (
            <div className="text-center py-8 space-y-2">
              <p className="text-sm text-destructive">{list.error?.message}</p>
              <Button variant="outline" size="sm" onClick={() => list.refetch()}>
                重试
              </Button>
            </div>
          ) : persons.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              暂无数据
            </div>
          ) : (
            persons.map((person) => (
              <div key={person.id} className="rounded-lg border bg-card p-4 space-y-3 overflow-hidden">
                {/* 复选框和状态 */}
                <div className="flex items-start gap-3">
                  <Checkbox
                    checked={list.isSelected(person.person_id)}
                    onCheckedChange={() => list.toggle(person.person_id)}
                    className="mt-1"
                  />
                  <div className="flex-1 min-w-0">
                    <div className={cn(
                      'inline-flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium mb-2',
                      person.is_known
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-400'
                    )}>
                      {person.is_known ? '已认识' : '未认识'}
                    </div>
                    <h3 className="font-semibold text-sm line-clamp-1 w-full break-all">
                      {person.person_name || <span className="text-muted-foreground">未命名</span>}
                    </h3>
                    {person.nickname && (
                      <p className="text-xs text-muted-foreground mt-1 line-clamp-1 w-full break-all">
                        昵称: {person.nickname}
                      </p>
                    )}
                  </div>
                </div>

                {/* 平台和用户信息 */}
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <div className="text-xs text-muted-foreground mb-1">平台</div>
                    <p className="font-medium text-xs">{person.platform}</p>
                  </div>
                  <div>
                    <div className="text-xs text-muted-foreground mb-1">用户ID</div>
                    <p className="font-mono text-xs truncate" title={person.user_id}>{person.user_id}</p>
                  </div>
                  <div className="col-span-2">
                    <div className="text-xs text-muted-foreground mb-1">最后更新</div>
                    <p className="text-xs">{formatTime(person.last_know)}</p>
                  </div>
                </div>

                {/* 操作按钮 */}
                <div className="flex flex-wrap gap-1 pt-2 border-t overflow-hidden">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleViewDetail(person)}
                    className="text-xs px-2 py-1 h-auto shrink-0"
                  >
                    <Eye className="h-3 w-3 mr-1" />
                    查看
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleEdit(person)}
                    className="text-xs px-2 py-1 h-auto shrink-0"
                  >
                    <Edit className="h-3 w-3 mr-1" />
                    编辑
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setDeleteConfirmPerson(person)}
                    className="text-xs px-2 py-1 h-auto shrink-0 text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-3 w-3 mr-1" />
                    删除
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 分页 - 增强版 */}
        {total > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-4 py-3 border-t">
            <div className="text-sm text-muted-foreground">
              共 {total} 条记录，第 {page} / {list.totalPages} 页
            </div>
            <div className="flex items-center gap-2">
              {/* 首页 */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => list.goToPage(1)}
                disabled={page === 1}
                className="hidden sm:flex"
              >
                <ChevronsLeft className="h-4 w-4" />
              </Button>

              {/* 上一页 */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => list.goToPage(page - 1)}
                disabled={page === 1}
              >
                <ChevronLeft className="h-4 w-4 sm:mr-1" />
                <span className="hidden sm:inline">上一页</span>
              </Button>

              {/* 页码跳转 */}
              <div className="flex items-center gap-2">
                <Input
                  type="number"
                  value={jumpToPage}
                  onChange={(e) => setJumpToPage(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleJumpToPage()}
                  placeholder={page.toString()}
                  className="w-16 h-8 text-center"
                  min={1}
                  max={list.totalPages}
                />
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleJumpToPage}
                  disabled={!jumpToPage}
                  className="h-8"
                >
                  跳转
                </Button>
              </div>

              {/* 下一页 */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => list.goToPage(page + 1)}
                disabled={page >= list.totalPages}
              >
                <span className="hidden sm:inline">下一页</span>
                <ChevronRight className="h-4 w-4 sm:ml-1" />
              </Button>

              {/* 末页 */}
              <Button
                variant="outline"
                size="sm"
                onClick={() => list.goToPage(list.totalPages)}
                disabled={page >= list.totalPages}
                className="hidden sm:flex"
              >
                <ChevronsRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </div>

        </div>
      </ScrollArea>

      {/* 详情对话框 */}
      <PersonDetailDialog
        person={selectedPerson}
        open={isDetailDialogOpen}
        onOpenChange={setIsDetailDialogOpen}
      />

      {/* 编辑对话框 */}
      <PersonEditDialog
        person={selectedPerson}
        open={isEditDialogOpen}
        onOpenChange={setIsEditDialogOpen}
        onSuccess={() => {
          list.invalidate()
          setIsEditDialogOpen(false)
        }}
      />

      {/* 删除确认对话框 */}
      <AlertDialog
        open={!!deleteConfirmPerson}
        onOpenChange={() => setDeleteConfirmPerson(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除人物信息 "{deleteConfirmPerson?.person_name || deleteConfirmPerson?.nickname || deleteConfirmPerson?.user_id}" 吗？
              此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => deleteConfirmPerson && deleteMutation.mutate(deleteConfirmPerson)}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* 批量删除确认对话框 */}
      <AlertDialog open={batchDeleteDialogOpen} onOpenChange={setBatchDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认批量删除</AlertDialogTitle>
            <AlertDialogDescription>
              确定要删除选中的 {list.selectedCount} 个人物信息吗？
              此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => batchDeleteMutation.mutate(Array.from(list.selectedIds))}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              批量删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

// 人物详情对话框
function PersonDetailDialog({
  person,
  open,
  onOpenChange,
}: {
  person: PersonInfo | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  if (!person) return null

  const formatTime = (timestamp: number | null) => {
    if (!timestamp) return '-'
    return new Date(timestamp * 1000).toLocaleString('zh-CN')
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>人物详情</DialogTitle>
          <DialogDescription>
            查看 {person.person_name || person.nickname || person.user_id} 的完整信息
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
        <div className="space-y-4">
          {/* 基本信息 */}
          <div className="grid grid-cols-2 gap-4">
            <InfoItem icon={User} label="人物名称" value={person.person_name} />
            <InfoItem icon={MessageSquare} label="昵称" value={person.nickname} />
            <InfoItem icon={Hash} label="用户ID" value={person.user_id} mono />
            <InfoItem icon={Hash} label="人物ID" value={person.person_id} mono />
            <InfoItem label="平台" value={person.platform} />
            <InfoItem label="状态" value={person.is_known ? '已认识' : '未认识'} />
          </div>

          {/* 名称原因 */}
          {person.name_reason && (
            <div className="rounded-lg border bg-muted/50 p-3">
              <Label className="text-xs text-muted-foreground">名称设定原因</Label>
              <p className="mt-1 text-sm">{person.name_reason}</p>
            </div>
          )}

          {/* 记忆点 */}
          {person.memory_points && (
            <div className="rounded-lg border bg-muted/50 p-3">
              <Label className="text-xs text-muted-foreground">个人印象</Label>
              <p className="mt-1 text-sm whitespace-pre-wrap">{person.memory_points}</p>
            </div>
          )}

          {/* 群昵称列表 */}
          {person.group_nick_name && person.group_nick_name.length > 0 && (
            <div className="rounded-lg border bg-muted/50 p-3">
              <Label className="text-xs text-muted-foreground">群昵称</Label>
              <div className="mt-2 space-y-1">
                {person.group_nick_name.map((item, index) => (
                  <div key={index} className="text-sm flex items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground">{item.group_id}</span>
                    <span>→</span>
                    <span>{item.group_nick_name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 时间信息 */}
          <div className="grid grid-cols-3 gap-4">
            <InfoItem icon={Clock} label="认识时间" value={formatTime(person.know_times)} />
            <InfoItem icon={Clock} label="首次记录" value={formatTime(person.know_since)} />
            <InfoItem icon={Clock} label="最后更新" value={formatTime(person.last_know)} />
          </div>
        </div>
        </DialogBody>

        <DialogFooter>
          <Button onClick={() => onOpenChange(false)}>关闭</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// 信息项组件
function InfoItem({
  icon: Icon,
  label,
  value,
  mono = false,
}: {
  icon?: typeof User
  label: string
  value: string | null | undefined
  mono?: boolean
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground flex items-center gap-1">
        {Icon && <Icon className="h-3 w-3" />}
        {label}
      </Label>
      <div className={cn('text-sm', mono && 'font-mono', !value && 'text-muted-foreground')}>
        {value || '-'}
      </div>
    </div>
  )
}

// 人物编辑对话框
function PersonEditDialog({
  person,
  open,
  onOpenChange,
  onSuccess,
}: {
  person: PersonInfo | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<PersonUpdateRequest>({})
  const { toast } = useToast()

  useEffect(() => {
    if (person) {
      setFormData({
        person_name: person.person_name || '',
        name_reason: person.name_reason || '',
        nickname: person.nickname || '',
        is_known: person.is_known,
      })
    }
  }, [person])

  // 保存（失败由全局 mutation 错误 toast 呈现）
  const updateMutation = useMutation({
    mutationFn: (vars: { personId: string; data: PersonUpdateRequest }) =>
      updatePerson(vars.personId, vars.data),
    meta: { errorTitle: '保存失败' },
    onSuccess: () => {
      toast({
        title: '保存成功',
        description: '人物信息已更新',
      })
      onSuccess()
    },
  })
  const saving = updateMutation.isPending

  const handleSave = () => {
    if (!person) return
    updateMutation.mutate({ personId: person.person_id, data: formData })
  }

  if (!person) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>编辑人物信息</DialogTitle>
          <DialogDescription>
            修改 {person.person_name || person.nickname || person.user_id} 的信息
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="person_name">人物名称</Label>
              <Input
                id="person_name"
                value={formData.person_name || ''}
                onChange={(e) => setFormData({ ...formData, person_name: e.target.value })}
                placeholder="为这个人设置一个名称"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="nickname">昵称</Label>
              <Input
                id="nickname"
                value={formData.nickname || ''}
                onChange={(e) => setFormData({ ...formData, nickname: e.target.value })}
                placeholder="昵称"
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="name_reason">名称设定原因</Label>
            <Textarea
              id="name_reason"
              value={formData.name_reason || ''}
              onChange={(e) => setFormData({ ...formData, name_reason: e.target.value })}
              placeholder="为什么这样称呼这个人？"
              rows={2}
            />
          </div>

          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <Label htmlFor="is_known" className="text-base font-medium">
                已认识
              </Label>
              <p className="text-sm text-muted-foreground">标记是否已经认识这个人</p>
            </div>
            <Switch
              id="is_known"
              checked={formData.is_known}
              onCheckedChange={(checked) => setFormData({ ...formData, is_known: checked })}
            />
          </div>
        </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
