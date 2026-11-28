import { useMutation, useQuery } from '@tanstack/react-query'
import {
  Clock,
  Edit,
  Eye,
  Hash,
  Link2,
  MessageSquare,
  Search,
  Trash2,
  User,
} from 'lucide-react'
import { Fragment, useEffect, useMemo, useState } from 'react'

import { ActionBar } from '@/components/ui/action-bar'
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
import { DataPagination } from '@/components/ui/data-pagination'
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
import { PillNav } from '@/components/ui/pill-nav'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ThinkingIllustration } from '@/components/ui/thinking-illustration'

import { useDataList } from '@/hooks/useDataList'
import { useToast } from '@/hooks/use-toast'

import {
  batchDeletePersons,
  deletePerson,
  getIndividual,
  getIndividualDirectory,
  mergeIndividuals,
  previewIndividualMerge,
  getPersonDetail,
  getPersonList,
  getPersonStats,
  updatePerson,
} from '@/lib/person-api'
import { cn } from '@/lib/utils'

import type { Individual, IndividualDirectoryEntry, IndividualMergePreview, IndividualProfile, ProfileField, PersonInfo, PersonUpdateRequest } from '@/types/person'

interface PersonFilters {
  profileVersion: 2
  view: 'individuals' | 'persons'
  linkedOnly: boolean
  known?: boolean
  platform?: string
}

function directoryKey(entry: IndividualDirectoryEntry): string {
  return `${entry.individual_id}:${entry.members[0]?.person_id ?? ''}`
}

const relativeTimeFormatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })

export function PersonManagementPage() {
  const [selectedPerson, setSelectedPerson] = useState<PersonInfo | null>(null)
  const [isDetailDialogOpen, setIsDetailDialogOpen] = useState(false)
  const [isEditDialogOpen, setIsEditDialogOpen] = useState(false)
  const [editScope, setEditScope] = useState<'individual' | 'account'>('account')
  const [deleteConfirmPerson, setDeleteConfirmPerson] = useState<PersonInfo | null>(null)
  const [batchDeleteDialogOpen, setBatchDeleteDialogOpen] = useState(false)
  const [mergePreview, setMergePreview] = useState<Individual[] | null>(null)
  const [mergeTarget, setMergeTarget] = useState('')
  const [mergeLoading, setMergeLoading] = useState(false)
  const [mergeSearch, setMergeSearch] = useState('')
  const [absoluteTime, setAbsoluteTime] = useState(false)
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 60_000)
    return () => window.clearInterval(timer)
  }, [])
  const { toast } = useToast()

  // 个体视图按完整成员组分页，账号视图按账号分页；筛选与翻页清空选择。
  const list = useDataList<IndividualDirectoryEntry, PersonFilters, string>({
    domain: 'persons',
    getId: directoryKey,
    searchDebounceMs: 250,
    initialFilters: { profileVersion: 2, view: 'individuals', linkedOnly: false, known: undefined, platform: undefined },
    queryFn: async ({ page, pageSize, search, filters }) => {
      const result = await getIndividualDirectory({
        page, page_size: pageSize, search,
        view: filters.view, linked_only: filters.linkedOnly,
        is_known: filters.known, platform: filters.platform,
      })
      return { items: result.data, total: result.total }
    },
  })
  const selectedEntries = list.items.filter(entry => list.isSelected(directoryKey(entry)))
  const selectedPersonIds = selectedEntries.flatMap(entry => entry.members.map(person => person.person_id))
  const grouped = list.filters.view === 'individuals'
  const statsQuery = useQuery({ queryKey: ['persons', 'stats', 'individuals-v1'], queryFn: getPersonStats })
  const stats = statsQuery.data

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
  const handleEdit = async (person: PersonInfo, scope: 'individual' | 'account' = 'account') => {
    try {
      setSelectedPerson(await getPersonDetail(person.person_id))
      setEditScope(scope)
      setIsEditDialogOpen(true)
    } catch (error) {
      toast({ title: '加载人物资料失败', description: error instanceof Error ? error.message : '无法打开编辑窗口', variant: 'destructive' })
    }
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
    return Object.keys(stats?.platforms ?? {})
  }, [stats?.platforms])

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

  const openMergeDialog = async (individualIds?: string[]) => {
    setMergeLoading(true)
    try {
      const ids = [...new Set(individualIds ?? selectedEntries.map(entry => entry.individual_id))]
      if (ids.length === 0) {
        toast({ title: '请选择要合并的账号' })
        return
      }
      const preview = await Promise.all(ids.map(getIndividual))
      setMergePreview(preview)
      setMergeSearch('')
      setMergeTarget(preview[0].individual_id)
    } catch (error) {
      toast({ title: '加载合并预览失败', description: error instanceof Error ? error.message : '无法加载成员', variant: 'destructive' })
    } finally {
      setMergeLoading(false)
    }
  }

  const mergeSearchQuery = useQuery({
    queryKey: ['persons', 'merge-search', mergeSearch],
    queryFn: () => getPersonList({ search: mergeSearch.trim(), page_size: 20 }),
    enabled: mergePreview !== null && mergeSearch.trim().length > 0,
  })

  const addMergePerson = async (person: PersonInfo) => {
    setMergeLoading(true)
    try {
      const individual = await getIndividual(person.individual_id)
      setMergePreview(current => current && !current.some(item => item.individual_id === individual.individual_id)
        ? [...current, individual] : current)
      setMergeSearch('')
    } catch (error) {
      toast({ title: '加载个体失败', description: error instanceof Error ? error.message : '无法加载成员', variant: 'destructive' })
    } finally {
      setMergeLoading(false)
    }
  }

  const mergePlanQuery = useQuery({
    queryKey: ['persons', 'merge-plan', mergeTarget, mergePreview?.map(item => item.individual_id)],
    queryFn: () => previewIndividualMerge(mergeTarget, mergePreview!.map(item => item.individual_id)),
    enabled: mergePreview !== null && Boolean(mergeTarget),
    staleTime: 0,
  })
  const [seenMergePlan, setSeenMergePlan] = useState<IndividualMergePreview | undefined>()
  const [finalProfile, setFinalProfile] = useState<IndividualProfile | null>(null)
  const [resolvedConflicts, setResolvedConflicts] = useState<ProfileField[]>([])
  const mergePlan = mergePlanQuery.data
  if (seenMergePlan !== mergePlan) {
    setSeenMergePlan(mergePlan)
    setFinalProfile(mergePlan?.profile ?? null)
    setResolvedConflicts([])
  }
  const setProfileField = <K extends ProfileField>(field: K, value: IndividualProfile[K]) => {
    setFinalProfile(current => current && ({ ...current, [field]: value }))
    setResolvedConflicts(current => [...new Set([...current, field])])
  }
  const pendingConflicts = mergePlan?.conflicts.filter(field => !resolvedConflicts.includes(field)) ?? []
  const fieldLabels: Record<ProfileField, string> = { display_name: '人物名称', is_known: '认识状态' }

  const mergeMutation = useMutation({
    mutationFn: () => {
      if (!mergePlan || !finalProfile || mergePlanQuery.isFetching || pendingConflicts.length) throw new Error('请先完成合并预览并处理冲突')
      return mergeIndividuals(mergePlan, finalProfile, resolvedConflicts)
    },
    meta: { errorTitle: '合并失败，请重新打开预览后重试' },
    onSuccess: (individual) => {
      toast({ title: '合并完成', description: `${individual.display_name} 已关联 ${individual.members.length} 个平台账号` })
      setMergePreview(null)
      setSelectedPerson(null)
      list.clearSelection()
      list.invalidate()
    },
  })

  // 最近互动默认使用相对时间，表头统一切换整列的显示方式。
  const formatTime = (timestamp: number | null): string => {
    if (timestamp === null) return '-'
    if (absoluteTime) return new Date(timestamp * 1000).toLocaleString('zh-CN')
    const seconds = (timestamp * 1000 - now) / 1000
    const units = [
      ['year', 365 * 86400], ['month', 30 * 86400], ['day', 86400],
      ['hour', 3600], ['minute', 60], ['second', 1],
    ] as const
    const [unit, duration] = units.find(([, size]) => Math.abs(seconds) >= size) ?? units[5]
    return relativeTimeFormatter.format(Math.trunc(seconds / duration), unit)
  }

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col gap-2 overflow-hidden p-3 sm:p-4">
      <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1">
        {stats && <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span><span className="font-medium text-foreground tabular-nums">{stats.individuals.toLocaleString()}</span> 个体</span>
          <span><span className="font-medium text-foreground tabular-nums">{stats.total.toLocaleString()}</span> 账号</span>
          <span className="tabular-nums">{stats.linked_individuals.toLocaleString()} 个体关联多个账号</span>
        </div>}
        {statsQuery.isError && <div role="alert" className="flex items-center gap-2 text-xs text-destructive">
          <span>{statsQuery.error.message}</span>
          <Button size="xs" variant="ghost" onClick={() => statsQuery.refetch()} disabled={statsQuery.isFetching}>重试</Button>
        </div>}
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-2" aria-label="视图与筛选">
        <Tabs value={list.filters.view} onValueChange={value => list.updateFilters(() => ({ profileVersion: 2, view: value as PersonFilters['view'], linkedOnly: false, known: undefined, platform: undefined }))}>
          <TabsList aria-label="查看方式" className="h-auto">
            <TabsTrigger value="individuals" size="sm">按个体分组</TabsTrigger>
            <TabsTrigger value="persons" size="sm">全部账号</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="relative min-w-48 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input aria-label="搜索个体或账号" placeholder="搜索个体、账号名称或 ID" controlSize="sm"
            value={list.searchInput} onChange={event => list.setSearchInput(event.target.value)} className="pl-8" />
        </div>
        {grouped && <Select value={list.filters.platform ?? 'all'} onValueChange={value => list.setFilter('platform', value === 'all' ? undefined : value)}>
          <SelectTrigger aria-label="平台筛选" className="w-32" controlSize="sm"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部平台</SelectItem>
            {platforms.map(platform => <SelectItem key={platform} value={platform}>{platform}</SelectItem>)}
          </SelectContent>
        </Select>}
        <Select value={list.filters.known === undefined ? 'all' : String(list.filters.known)}
          onValueChange={value => list.setFilter('known', value === 'all' ? undefined : value === 'true')}>
          <SelectTrigger aria-label="认识状态筛选" className="w-32" controlSize="sm"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部状态</SelectItem>
            <SelectItem value="true">已认识</SelectItem>
            <SelectItem value="false">未认识</SelectItem>
          </SelectContent>
        </Select>
        {grouped && <label htmlFor="person-linked-only" className="flex cursor-pointer items-center gap-1.5 whitespace-nowrap text-xs">
          <Checkbox id="person-linked-only" checked={list.filters.linkedOnly} onCheckedChange={value => list.setFilter('linkedOnly', value === true)} />
          仅多账号个体
        </label>}
      </div>

      {!grouped && (
        <div className="shrink-0 border-b pb-2">
          <PillNav
            as="nav"
            variant="pill"
            size="xs"
            scrollable
            ariaLabel="按平台查看账号"
            value={list.filters.platform ?? '__all__'}
            options={[
              {
                value: '__all__',
                label: '全部平台',
                count: stats?.total,
                ariaLabel: `全部平台 ${stats?.total ?? '—'}`,
              },
              ...platforms.map((platform) => ({
                value: platform,
                label: platform,
                count: stats?.platforms[platform],
                ariaLabel:
                  stats?.platforms[platform] !== undefined
                    ? `${platform} ${stats.platforms[platform]}`
                    : platform,
              })),
            ]}
            onChange={(next) => list.setFilter('platform', next === '__all__' ? undefined : next)}
          />
        </div>
      )}

      <ActionBar
        open={list.selectedCount > 0}
        countLabel={`已选 ${grouped ? `${list.selectedCount} 个体 · ` : ''}${selectedPersonIds.length} 账号`}
        onClear={list.clearSelection}
      >
        <Button
          variant="outline"
          size="xs"
          disabled={mergeLoading || list.isFetching}
          onClick={() => openMergeDialog()}
        >
          合并个体
        </Button>
        <Button
          variant="destructive"
          size="xs"
          disabled={list.isFetching}
          onClick={openBatchDeleteDialog}
        >
          删除所选账号
        </Button>
      </ActionBar>

      <section aria-label="个体与账号对应关系" className="min-h-0 flex-1 overflow-hidden rounded-md [&>div]:h-full">
        <Table density="compact" className="min-w-[900px]" aria-label={grouped ? '个体及其全部账号' : '全部账号及所属个体'}>
          <TableHeader className="sticky top-0 z-10 bg-background [&_th]:whitespace-nowrap">
            <TableRow>
              <TableHead className="w-10"><Checkbox aria-label="全选当前页" disabled={list.isFetching}
                checked={list.items.length > 0 && list.selectedCount === list.items.length} onCheckedChange={list.toggleAll} /></TableHead>
              {grouped && <TableHead className="w-52">人物</TableHead>}
              <TableHead>账号</TableHead>
              <TableHead className="w-24">平台</TableHead>
              <TableHead>ID</TableHead>
              {!grouped && <TableHead className="w-52">所属人物</TableHead>}
              <TableHead className="w-36">
                <button type="button" className="cursor-pointer hover:text-foreground" aria-pressed={absoluteTime}
                  title={`当前为${absoluteTime ? '绝对' : '相对'}时间，点击切换为${absoluteTime ? '相对' : '绝对'}时间`}
                  aria-label={`最近互动，切换为${absoluteTime ? '相对' : '绝对'}时间`}
                  onClick={() => setAbsoluteTime(value => !value)}>最近互动</button>
              </TableHead>
              <TableHead className="w-28 text-right"><span className="sr-only">操作</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.isPending ? <TableRow><TableCell colSpan={7} className="py-10 text-center"><ThinkingIllustration size="sm" className="mx-auto" /></TableCell></TableRow>
              : list.isError ? <TableRow><TableCell colSpan={7} className="py-10 text-center">
                <p className="mb-2 text-destructive">{list.error?.message}</p><Button size="sm" variant="outline" onClick={list.refetch}>重试</Button>
              </TableCell></TableRow>
              : list.items.length === 0 ? <TableRow><TableCell colSpan={7} className="py-10 text-center text-muted-foreground">没有匹配的个体或账号</TableCell></TableRow>
              : list.items.map(entry => <Fragment key={directoryKey(entry)}>
                {entry.members.map((person, index) => <TableRow key={person.person_id} data-state={list.isSelected(directoryKey(entry)) ? 'selected' : undefined}>
                  {index === 0 && <>
                    <TableCell rowSpan={entry.members.length} className="align-top">
                      <Checkbox checked={list.isSelected(directoryKey(entry))} disabled={list.isFetching}
                        onCheckedChange={() => list.toggle(directoryKey(entry))}
                        aria-label={`选择${grouped ? '个体' : '账号'} ${grouped ? entry.display_name : person.nickname || person.source_person_name || person.user_id}`} />
                    </TableCell>
                    {grouped && <TableCell rowSpan={entry.members.length} className="border-r bg-muted/30 align-top">
                      <div className="max-w-52">
                        <div className="flex min-w-0 items-center justify-between gap-1">
                          <span className="truncate font-medium" title={entry.display_name}>{entry.display_name}</span>
                          <Button variant="ghost" size="icon-xs" title="编辑人物资料" aria-label={`编辑人物 ${entry.display_name}`}
                            onClick={() => handleEdit(entry.members[0], 'individual')}><Edit /></Button>
                          <Button variant="ghost" size="icon-xs" className="shrink-0" disabled={mergeLoading || list.isFetching}
                            aria-label={`为 ${entry.display_name} 关联其他账号`} title="关联其他账号"
                            onClick={() => openMergeDialog([entry.individual_id])}><Link2 /></Button>
                        </div>
                      </div>
                    </TableCell>}
                  </>}
                  <TableCell className="max-w-64">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className={cn('h-1.5 w-1.5 shrink-0 rounded-full', person.is_known ? 'bg-emerald-500' : 'bg-muted-foreground')}
                        title={person.is_known ? '已认识' : '未认识'} role="img" aria-label={person.is_known ? '已认识' : '未认识'} />
                      <button className="truncate text-left font-medium hover:underline" onClick={() => handleViewDetail(person)}
                        title={person.nickname || person.source_person_name || person.user_id}>{person.nickname || person.source_person_name || person.user_id}</button>
                    </div>
                    {person.nickname && person.source_person_name && person.source_person_name !== person.nickname &&
                      <div className="ml-3.5 truncate text-xs text-muted-foreground" title={person.source_person_name}>{person.source_person_name}</div>}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs">{person.platform}</TableCell>
                  <TableCell className="max-w-56"><span className="block truncate font-mono text-xs" title={person.user_id}>{person.user_id}</span></TableCell>
                  {!grouped && <TableCell>
                    <button className="text-left text-sm hover:underline" aria-label={`${entry.member_count} 个账号 · 查看关系`} title="查看这个人物的全部关联账号" onClick={() => {
                      list.updateFilters(() => ({ profileVersion: 2, view: 'individuals', linkedOnly: false, known: undefined, platform: undefined }))
                      list.setSearchInput(entry.individual_id)
                    }}>
                      <span className="block">{entry.display_name}</span>
                      <span className="text-xs text-muted-foreground">{entry.member_count} 个账号 · 查看关系</span>
                    </button>
                  </TableCell>}
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground"
                    title={person.last_know === null ? undefined : new Date(person.last_know * 1000).toLocaleString('zh-CN')}>
                    {formatTime(person.last_know)}
                  </TableCell>
                  <TableCell><div className="flex justify-end gap-0.5">
                    <Button variant="ghost" size="icon-xs" aria-label={`查看账号 ${person.user_id}`} title="详情" onClick={() => handleViewDetail(person)}><Eye /></Button>
                    <Button variant="ghost" size="icon-xs" aria-label={`编辑账号 ${person.user_id}`} title="编辑" onClick={() => handleEdit(person)}><Edit /></Button>
                    <Button variant="ghost" size="icon-xs" aria-label={`删除账号 ${person.user_id}`} title="删除" onClick={() => setDeleteConfirmPerson(person)}><Trash2 /></Button>
                  </div></TableCell>
                </TableRow>)}
              </Fragment>)}
          </TableBody>
        </Table>
      </section>
      <footer className="flex shrink-0 items-center justify-end">
        <DataPagination compact total={list.total} page={list.page} pageSize={list.pageSize}
          onPageChange={list.setPage} onPageSizeChange={list.setPageSize} disabled={list.isFetching} />
      </footer>

      <Dialog open={mergePreview !== null} onOpenChange={open => { if (!open && !mergeMutation.isPending) setMergePreview(null) }}>
        <DialogContent className="max-w-2xl" aria-describedby={undefined}>
          <DialogTitle className="sr-only">合并账号</DialogTitle>
          <DialogBody>
            <Label htmlFor="merge-person-search">添加要合并的账号</Label>
            <Input id="merge-person-search" className="mt-2 mb-3" placeholder="按名称、昵称或用户 ID 搜索其他账号"
              value={mergeSearch} onChange={event => setMergeSearch(event.target.value)}
              disabled={mergeMutation.isPending || mergeLoading} />
            {mergeSearch.trim() && <div className="mb-4 space-y-1">
              {mergeSearchQuery.isFetching && <p className="text-sm text-muted-foreground">搜索中...</p>}
              {mergeSearchQuery.isError && <p className="text-sm text-destructive">搜索失败：{mergeSearchQuery.error.message}</p>}
              {mergeSearchQuery.data?.data.map(person => (
                <Button key={person.person_id} variant="outline" className="w-full justify-start gap-2"
                  disabled={mergeMutation.isPending || mergeLoading || mergePreview?.some(item => item.individual_id === person.individual_id)}
                  onClick={() => addMergePerson(person)}>
                  <span>{person.nickname || person.source_person_name || person.user_id}</span>
                  <span className="text-xs text-muted-foreground">{person.platform}</span>
                  <span className="font-mono text-xs text-muted-foreground">{person.user_id}</span>
                </Button>
              ))}
              {mergeSearchQuery.data?.total === 0 && <p className="text-sm text-muted-foreground">没有匹配的账号</p>}
              {(mergeSearchQuery.data?.total ?? 0) > 20 && <p className="text-sm text-muted-foreground">仅显示前 20 条，请输入更精确的名称或用户 ID。</p>}
            </div>}
            <Label>合并到</Label>
            <Select value={mergeTarget} onValueChange={setMergeTarget} disabled={mergeMutation.isPending}>
              <SelectTrigger className="mt-2"><SelectValue /></SelectTrigger>
              <SelectContent>
                {mergePreview?.map(individual => (
                  <SelectItem key={individual.individual_id} value={individual.individual_id}>
                    {individual.display_name} · {individual.members.map(person => `${person.platform}:${person.user_id}`).join('、')}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {mergePlanQuery.isFetching && <p className="mt-3 text-sm text-muted-foreground">正在生成合并预览…</p>}
            {mergePlanQuery.isError && <div role="alert" className="mt-3 text-sm text-destructive">
              {mergePlanQuery.error.message}<Button size="xs" variant="ghost" onClick={() => mergePlanQuery.refetch()}>重新预览</Button>
            </div>}
            {mergePlan && finalProfile && !mergePlanQuery.isError && <div className="mt-4 space-y-4 rounded-md border p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">合并后的内容</span>
                <span className="text-xs text-muted-foreground">{Object.keys(mergePlan.members).length} 个账号</span>
              </div>
              {(['display_name', 'is_known'] as const).map(field => <div key={field} className="space-y-2">
                <div className="flex items-center gap-2">
                  <Label htmlFor={`merged-${field}`}>{fieldLabels[field]}</Label>
                  {mergePlan.conflicts.includes(field) && <span className={cn('text-xs', pendingConflicts.includes(field) ? 'text-destructive' : 'text-muted-foreground')}>
                    {pendingConflicts.includes(field) ? '存在冲突，请选择来源或编辑' : '已确认'}
                  </span>}
                </div>
                {field === 'display_name' ? <Input id={`merged-${field}`} value={finalProfile.display_name} maxLength={255}
                  disabled={mergeMutation.isPending || mergePlanQuery.isFetching} onChange={event => setProfileField(field, event.target.value)} />
                  : <div className="flex items-center gap-2 text-sm"><Switch id={`merged-${field}`} checked={finalProfile.is_known}
                      disabled={mergeMutation.isPending || mergePlanQuery.isFetching} onCheckedChange={value => setProfileField(field, value)} />
                      {finalProfile.is_known ? '已认识' : '未认识'}</div>}
                {mergePlan.conflicts.includes(field) && <div className="flex flex-wrap gap-1">
                  {mergePlan.sources.map(source => <Button key={source.individual_id} size="xs" variant="outline" className="h-auto max-w-full whitespace-normal break-words py-1 text-left"
                    disabled={mergeMutation.isPending || mergePlanQuery.isFetching}
                    title={String(source[field] ?? '无')}
                    onClick={() => setProfileField(field, source[field])}>
                    采用 {source.display_name}：{field === 'is_known' ? (source.is_known ? '已认识' : '未认识') : (source[field] || '无')}
                  </Button>)}
                </div>}
              </div>)}
              {pendingConflicts.length > 0 && <p className="text-xs text-destructive">还有 {pendingConflicts.length} 项冲突待处理</p>}
            </div>}
            <div className="mt-4 space-y-3">
              {mergePreview?.map(individual => (
                <div key={individual.individual_id} className="rounded-lg border p-3">
                  <p className="font-medium">{individual.display_name}</p>
                  <p className="text-xs text-muted-foreground">{individual.is_known ? '已认识' : '未认识'}</p>
                  {individual.members.map(person => (
                    <p key={person.person_id} className="flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
                      <span>{person.nickname || person.source_person_name || person.user_id}</span>
                      <span className="text-xs">{person.platform}</span>
                      <span className="font-mono text-xs">{person.user_id}</span>
                    </p>
                  ))}
                </div>
              ))}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" disabled={mergeMutation.isPending} onClick={() => setMergePreview(null)}>取消</Button>
            <Button disabled={mergeMutation.isPending || mergeLoading || mergePlanQuery.isFetching || mergePlanQuery.isError || !mergePlan || !finalProfile?.display_name.trim() || pendingConflicts.length > 0 || (mergePreview?.length ?? 0) < 2} onClick={() => mergeMutation.mutate()}>
              {mergeMutation.isPending ? '合并中...' : '确认合并全部账号'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* 详情对话框 */}
      <PersonDetailDialog
        person={selectedPerson}
        open={isDetailDialogOpen}
        onOpenChange={setIsDetailDialogOpen}
      />

      {/* 编辑对话框 */}
      <PersonEditDialog
        person={selectedPerson}
        scope={editScope}
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
              确定要删除选中的 {selectedPersonIds.length} 个账号信息吗？
              此操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => batchDeleteMutation.mutate(selectedPersonIds)}
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
  const individualQuery = useQuery({
    queryKey: ['persons', 'individual', 'profile-v2', person?.individual_id],
    queryFn: () => getIndividual(person!.individual_id),
    enabled: open && !!person,
  })
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

          <div className="rounded-lg border p-3 space-y-2">
            <Label>统一个体：{individualQuery.data?.display_name || '加载中'}</Label>
            <p className="text-xs text-muted-foreground break-all">{person.individual_id}</p>
            {individualQuery.isError && <p className="text-sm text-destructive">加载关联账号失败：{individualQuery.error.message}</p>}
            {individualQuery.data?.members.map(member => (
              <p key={member.person_id} className="flex flex-wrap items-center gap-x-2 text-sm">
                <span>{member.nickname || member.source_person_name || member.user_id}</span>
                <span className="text-xs text-muted-foreground">{member.platform}</span>
                <span className="font-mono text-xs text-muted-foreground">{member.user_id}</span>
              </p>
            ))}
          </div>

          {individualQuery.data && individualQuery.data.profile_sources.length > 0 && <details className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm">人物资料来源</summary>
            <div className="mt-2 space-y-2">
              {individualQuery.data.profile_sources.map((source, index) => <div key={`${source.person_id}:${index}`} className="text-xs">
                <p className="flex flex-wrap items-center gap-x-2">
                  <span>{source.display_name}</span>
                  <span className="text-muted-foreground">{source.platform}</span>
                  <span className="font-mono text-muted-foreground">{source.user_id}</span>
                </p>
                <p className="text-muted-foreground">{source.is_known ? '已认识' : '未认识'}</p>
              </div>)}
            </div>
          </details>}

          {/* 群昵称列表 */}
          {person.group_nick_name && person.group_nick_name.length > 0 && (
            <div className="rounded-lg border bg-muted/50 p-3">
              <Label className="text-xs text-muted-foreground">群昵称</Label>
              <div className="mt-2 space-y-1">
                {person.group_nick_name.map((item, index) => (
                  <div key={index} className="text-sm flex items-center gap-2">
                    <span className="font-mono text-xs text-muted-foreground">{item.group_id}</span>
                    <span>→</span>
                    <span>{item.group_cardname}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 认识次数与时间 */}
          <div className="grid grid-cols-3 gap-4">
            <InfoItem icon={MessageSquare} label="认识次数" value={person.know_times === null ? '-' : `${person.know_times} 次`} />
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
  scope,
  open,
  onOpenChange,
  onSuccess,
}: {
  scope: 'individual' | 'account'
  person: PersonInfo | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSuccess: () => void
}) {
  const [formData, setFormData] = useState<PersonUpdateRequest>({})
  const [seenPerson, setSeenPerson] = useState(person)
  const { toast } = useToast()
  if (person && person !== seenPerson) {
    setSeenPerson(person)
    setFormData({
      person_name: person.person_name || '',
      nickname: person.nickname || '',
      is_known: person.is_known,
    })
  }

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
    updateMutation.mutate({ personId: person.person_id, data: {
      ...(scope === 'account' ? { nickname: formData.nickname } : {
        person_name: formData.person_name, is_known: formData.is_known,
        expected_individual_id: person.individual_id,
        expected_profile: { display_name: person.person_name!, is_known: person.is_known },
      }),
    } })
  }

  if (!person) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" confirmOnEnter>
        <DialogHeader>
          <DialogTitle>{scope === 'individual' ? '编辑人物资料' : '编辑账号资料'}</DialogTitle>
          <DialogDescription>
            {scope === 'individual' ? '人物名称供麦麦识别人物，所有关联账号共享。不会修改平台昵称。' : `${person.platform} · ${person.user_id}。昵称是本地保存的平台账号名称，不修改所属人物。`}
          </DialogDescription>
        </DialogHeader>

        <DialogBody>
        <div className="space-y-4">
          <div className="space-y-4">
            {scope === 'individual' && <div className="space-y-2">
              <Label htmlFor="person_name">人物名称</Label>
              <Input
                id="person_name"
                value={formData.person_name || ''}
                onChange={(e) => setFormData({ ...formData, person_name: e.target.value })}
                placeholder="为这个人设置一个名称"
              />
            </div>}
            {scope === 'account' && <div className="space-y-2">
              <Label htmlFor="nickname">账号昵称</Label>
              <Input
                id="nickname"
                value={formData.nickname || ''}
                onChange={(e) => setFormData({ ...formData, nickname: e.target.value })}
                placeholder="账号昵称"
              />
            </div>}
          </div>

          {scope === 'individual' && <>
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
          </div></>}
        </div>
        </DialogBody>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving || (scope === 'individual' && !formData.person_name?.trim())}>
            {saving ? '保存中...' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
