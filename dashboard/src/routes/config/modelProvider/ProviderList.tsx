import { useCallback, useMemo, useState, type ReactNode } from 'react'
import type { TestConnectionResult } from '@/lib/config-api'
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Loader2, Pencil, Search, Trash2, XCircle, Zap } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

import { ProviderCard } from './ProviderCard'
import type { APIProvider } from './types'

interface ProviderListProps {
  providers: APIProvider[]
  testingProviders: Set<string>
  testResults: Map<string, TestConnectionResult>
  selectedProviders: Set<number>
  toolbarActions?: ReactNode
  onEdit: (provider: APIProvider, index: number) => void
  onDelete: (index: number) => void
  onTest: (name: string) => void
  onToggleSelect: (index: number) => void
  onToggleSelectAll: () => void
}

const providerTableHeadClass = 'h-10 whitespace-nowrap px-3 text-xs font-semibold'
const providerTableCellClass = 'px-3 py-2.5'
const providerNumericHeadClass = `${providerTableHeadClass} w-20 text-right`
const providerNumericCellClass = `${providerTableCellClass} text-right tabular-nums`

export function ProviderList({
  providers,
  testingProviders,
  testResults,
  selectedProviders,
  toolbarActions,
  onEdit,
  onDelete,
  onTest,
  onToggleSelect,
  onToggleSelectAll,
}: ProviderListProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [jumpToPage, setJumpToPage] = useState('')

  const filteredProviders = useMemo(() => {
    if (!searchQuery) return providers
    const query = searchQuery.toLowerCase()
    return providers.filter((provider) => (
      provider.name.toLowerCase().includes(query) ||
      provider.base_url.toLowerCase().includes(query) ||
      provider.client_type.toLowerCase().includes(query)
    ))
  }, [providers, searchQuery])

  const { totalPages, paginatedProviders } = useMemo(() => {
    const total = Math.ceil(filteredProviders.length / pageSize)
    const paginated = filteredProviders.slice(
      (page - 1) * pageSize,
      page * pageSize
    )
    return { totalPages: total, paginatedProviders: paginated }
  }, [filteredProviders, page, pageSize])

  const handleJumpToPage = useCallback(() => {
    const targetPage = parseInt(jumpToPage)
    if (targetPage >= 1 && targetPage <= totalPages) {
      setPage(targetPage)
      setJumpToPage('')
    }
  }, [jumpToPage, totalPages])

  const renderTestStatus = (providerName: string) => {
    const isTesting = testingProviders.has(providerName)
    const result = testResults.get(providerName)

    if (isTesting) {
      return (
        <Badge variant="secondary" className="gap-1">
          <Loader2 className="h-3 w-3 animate-spin" />
          测试中
        </Badge>
      )
    }

    if (!result) {
      return (
        <Badge variant="outline" className="text-muted-foreground">
          未测试
        </Badge>
      )
    }

    if (result.network_ok) {
      if (result.api_key_valid === true) {
        return (
          <Badge className="gap-1 bg-green-600 hover:bg-green-700">
            <CheckCircle2 className="h-3 w-3" />
            正常
          </Badge>
        )
      } else if (result.api_key_valid === false) {
        return (
          <Badge variant="destructive" className="gap-1">
            <AlertCircle className="h-3 w-3" />
            Key无效
          </Badge>
        )
      } else {
        return (
          <Badge className="gap-1 bg-blue-600 hover:bg-blue-700">
            <CheckCircle2 className="h-3 w-3" />
            可访问
          </Badge>
        )
      }
    } else {
      return (
        <Badge variant="destructive" className="gap-1">
          <XCircle className="h-3 w-3" />
          离线
        </Badge>
      )
    }
  }

  return (
    <>
      {/* 搜索框 */}
      <div className="mb-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center">
          <div className="relative w-full sm:flex-1 sm:max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="搜索提供商名称、URL 或类型..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-9"
            />
          </div>
          {searchQuery && (
            <p className="text-sm text-muted-foreground whitespace-nowrap">
              找到 {filteredProviders.length} 个结果
            </p>
          )}
        </div>
        {toolbarActions && (
          <div className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row sm:items-center sm:justify-end">
            {toolbarActions}
          </div>
        )}
      </div>

      {/* 移动端卡片视图 */}
      <div className="md:hidden space-y-3">
        {filteredProviders.length === 0 ? (
          <div className="text-center text-muted-foreground py-8 rounded-lg border bg-card">
            {searchQuery ? '未找到匹配的提供商' : '暂无提供商配置，点击"添加提供商"开始配置'}
          </div>
        ) : (
          paginatedProviders.map((provider, displayIndex) => {
            const actualIndex = providers.findIndex(p => p === provider)
            return (
              <ProviderCard
                key={displayIndex}
                provider={provider}
                actualIndex={actualIndex}
                testingProviders={testingProviders}
                testResults={testResults}
                onEdit={onEdit}
                onDelete={onDelete}
                onTest={onTest}
              />
            )
          })
        )}
      </div>

      {/* 桌面端表格视图 */}
      <div className="hidden md:block overflow-hidden border bg-card">
        <div className="overflow-x-auto">
          <Table aria-label="AI 模型提供商列表" className="min-w-[960px]">
            <TableHeader className="bg-muted/30">
              <TableRow className="hover:bg-transparent">
                <TableHead className={`${providerTableHeadClass} w-[5.5rem]`}>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      checked={selectedProviders.size === filteredProviders.length && filteredProviders.length > 0}
                      onCheckedChange={onToggleSelectAll}
                      aria-label="选择全部厂商用于批量删除"
                      title="选择全部厂商用于批量删除"
                    />
                    <span>选择</span>
                  </div>
                </TableHead>
                <TableHead className={`${providerTableHeadClass} w-24`}>状态</TableHead>
                <TableHead className={`${providerTableHeadClass} w-32`}>名称</TableHead>
                <TableHead className={providerTableHeadClass}>基础URL</TableHead>
                <TableHead className={`${providerTableHeadClass} w-24`}>客户端</TableHead>
                <TableHead className={providerNumericHeadClass}>最大重试</TableHead>
                <TableHead className={providerNumericHeadClass}>超时(秒)</TableHead>
                <TableHead className={providerNumericHeadClass}>间隔(秒)</TableHead>
                <TableHead className={`${providerTableHeadClass} w-48 text-right`}>操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {paginatedProviders.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={9} className="text-center text-muted-foreground py-8">
                    {searchQuery ? '未找到匹配的提供商' : '暂无提供商配置，点击"添加提供商"开始配置'}
                  </TableCell>
                </TableRow>
              ) : (
                paginatedProviders.map((provider, displayIndex) => {
                  const actualIndex = providers.findIndex(p => p === provider)
                  return (
                    <TableRow key={displayIndex} data-state={selectedProviders.has(actualIndex) ? 'selected' : undefined}>
                      <TableCell className={providerTableCellClass}>
                        <Checkbox
                          checked={selectedProviders.has(actualIndex)}
                          onCheckedChange={() => onToggleSelect(actualIndex)}
                          aria-label={`选择厂商 ${provider.name} 用于批量删除`}
                          title="选择后可批量删除厂商"
                        />
                      </TableCell>
                      <TableCell className={providerTableCellClass}>
                        {renderTestStatus(provider.name)}
                      </TableCell>
                      <TableCell className={`${providerTableCellClass} font-medium whitespace-nowrap`}>{provider.name}</TableCell>
                      <TableCell className={`${providerTableCellClass} max-w-[22rem] truncate font-mono text-xs`} title={provider.base_url}>
                        {provider.base_url}
                      </TableCell>
                      <TableCell className={`${providerTableCellClass} whitespace-nowrap`}>{provider.client_type}</TableCell>
                      <TableCell className={providerNumericCellClass}>{provider.max_retry}</TableCell>
                      <TableCell className={providerNumericCellClass}>{provider.timeout}</TableCell>
                      <TableCell className={providerNumericCellClass}>{provider.retry_interval}</TableCell>
                      <TableCell className={`${providerTableCellClass} text-right`}>
                        <div className="flex justify-end gap-2">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => onTest(provider.name)}
                            disabled={testingProviders.has(provider.name)}
                            title="测试连接"
                          >
                            {testingProviders.has(provider.name) ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Zap className="h-4 w-4" />
                            )}
                          </Button>
                          <Button
                            variant="default"
                            size="sm"
                            onClick={() => onEdit(provider, actualIndex)}
                          >
                            <Pencil className="h-4 w-4 mr-1" strokeWidth={2} fill="none" />
                            编辑
                          </Button>
                          <Button
                            size="sm"
                            onClick={() => onDelete(actualIndex)}
                            className="bg-red-600 hover:bg-red-700 text-white"
                          >
                            <Trash2 className="h-4 w-4 mr-1" strokeWidth={2} fill="none" />
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      {/* 分页 */}
      {filteredProviders.length > 0 && (
        <div className="flex flex-col sm:flex-row items-center justify-between gap-4 mt-4">
          <div className="flex items-center gap-2">
            <Label htmlFor="page-size-provider" className="text-sm whitespace-nowrap">每页显示</Label>
            <Select
              value={pageSize.toString()}
              onValueChange={(value) => {
                setPageSize(parseInt(value))
                setPage(1)
              }}
            >
              <SelectTrigger id="page-size-provider" className="w-20">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
            <span className="text-sm text-muted-foreground">
              显示 {(page - 1) * pageSize + 1} 到{' '}
              {Math.min(page * pageSize, filteredProviders.length)} 条，共 {filteredProviders.length} 条
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(1)}
              disabled={page === 1}
              className="hidden sm:flex"
            >
              <ChevronsLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page === 1}
            >
              <ChevronLeft className="h-4 w-4 sm:mr-1" />
              <span className="hidden sm:inline">上一页</span>
            </Button>
            <div className="flex items-center gap-2">
              <Input
                type="number"
                value={jumpToPage}
                onChange={(e) => setJumpToPage(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleJumpToPage()}
                placeholder={page.toString()}
                className="w-16 h-8 text-center"
                min={1}
                max={totalPages}
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
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => p + 1)}
              disabled={page >= totalPages}
            >
              <span className="hidden sm:inline">下一页</span>
              <ChevronRight className="h-4 w-4 sm:ml-1" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage(totalPages)}
              disabled={page >= totalPages}
              className="hidden sm:flex"
            >
              <ChevronsRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </>
  )
}
