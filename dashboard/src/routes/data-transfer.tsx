import type { Dispatch, SetStateAction } from 'react'
import { Archive, Download, RefreshCw, Upload, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useToast } from '@/hooks/use-toast'
import {
  cancelDataExportJob,
  createDataExportJob,
  createDataImportJob,
  downloadDataExport,
  getDataTransferJob,
  type DataTransferJob,
} from '@/lib/data-transfer-api'

function formatStorageBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return '0 B'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const unitIndex = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** unitIndex
  return `${value.toFixed(value >= 10 || unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`
}

function isTransferJobRunning(job: DataTransferJob | null): job is DataTransferJob {
  return job?.status === 'pending' || job?.status === 'running'
}

function useTransferJobPolling(
  job: DataTransferJob | null,
  setJob: Dispatch<SetStateAction<DataTransferJob | null>>,
  refreshJob: (job: DataTransferJob, setter: (value: DataTransferJob) => void) => Promise<void>
) {
  useEffect(() => {
    if (!isTransferJobRunning(job)) return

    let cancelled = false
    let timerId: number | null = null
    const poll = async () => {
      await refreshJob(job, setJob)
      if (!cancelled) {
        timerId = window.setTimeout(() => void poll(), 1200)
      }
    }

    timerId = window.setTimeout(() => void poll(), 1200)
    return () => {
      cancelled = true
      if (timerId !== null) {
        window.clearTimeout(timerId)
      }
    }
  }, [job, refreshJob, setJob])
}

export function DataTransferPage() {
  const { toast } = useToast()
  const [exportIncludePlugins, setExportIncludePlugins] = useState(false)
  const [exportIncludeLogs, setExportIncludeLogs] = useState(false)
  const [exportJob, setExportJob] = useState<DataTransferJob | null>(null)
  const [exportCreating, setExportCreating] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importConfig, setImportConfig] = useState(true)
  const [importData, setImportData] = useState(true)
  const [importPlugins, setImportPlugins] = useState(false)
  const [importLogs, setImportLogs] = useState(false)
  const [importJob, setImportJob] = useState<DataTransferJob | null>(null)
  const [importUploading, setImportUploading] = useState(false)
  const [importUploadProgress, setImportUploadProgress] = useState(0)

  const refreshTransferJob = useCallback(
    async (job: DataTransferJob, setter: (value: DataTransferJob) => void) => {
      try {
        const latestJob = await getDataTransferJob(job.job_id)
        setter(latestJob)
        if (latestJob.status === 'failed') {
          toast({
            title: latestJob.kind === 'export' ? '导出失败' : '导入失败',
            description: latestJob.error || latestJob.message,
            variant: 'destructive',
          })
        }
      } catch (error) {
        toast({
          title: '任务进度刷新失败',
          description: error instanceof Error ? error.message : '无法读取数据迁移任务状态',
          variant: 'destructive',
        })
      }
    },
    [toast]
  )

  useTransferJobPolling(exportJob, setExportJob, refreshTransferJob)
  useTransferJobPolling(importJob, setImportJob, refreshTransferJob)

  const handleCreateExport = useCallback(async () => {
    try {
      setExportCreating(true)
      const job = await createDataExportJob({
        include_plugins: exportIncludePlugins,
        include_logs: exportIncludeLogs,
      })
      setExportJob(job)
      toast({ title: '已开始导出 MaiBot 数据' })
    } catch (error) {
      toast({
        title: '创建导出任务失败',
        description: error instanceof Error ? error.message : '无法创建导出任务',
        variant: 'destructive',
      })
    } finally {
      setExportCreating(false)
    }
  }, [exportIncludeLogs, exportIncludePlugins, toast])

  const handleDownloadExport = useCallback(async () => {
    if (!exportJob) return
    try {
      await downloadDataExport(exportJob)
    } catch (error) {
      toast({
        title: '下载失败',
        description: error instanceof Error ? error.message : '无法下载导出文件',
        variant: 'destructive',
      })
    }
  }, [exportJob, toast])

  const handleCancelExport = useCallback(async () => {
    if (!exportJob || !isTransferJobRunning(exportJob)) return
    try {
      const job = await cancelDataExportJob(exportJob.job_id)
      setExportJob(job)
      toast({ title: '正在取消导出' })
    } catch (error) {
      toast({
        title: '取消导出失败',
        description: error instanceof Error ? error.message : '无法取消当前导出任务',
        variant: 'destructive',
      })
    }
  }, [exportJob, toast])

  const handleCreateImport = useCallback(async () => {
    if (!importFile) {
      toast({ title: '请选择要导入的压缩包', variant: 'destructive' })
      return
    }
    if (!importConfig && !importData && !importPlugins && !importLogs) {
      toast({ title: '请至少选择一个导入范围', variant: 'destructive' })
      return
    }
    try {
      setImportUploading(true)
      setImportUploadProgress(0)
      const response = await createDataImportJob(
        importFile,
        {
          import_config: importConfig,
          import_data: importData,
          import_plugins: importPlugins,
          import_logs: importLogs,
        },
        setImportUploadProgress
      )
      const job = await getDataTransferJob(response.job_id)
      setImportJob(job)
      toast({ title: '已开始导入 MaiBot 数据' })
    } catch (error) {
      toast({
        title: '创建导入任务失败',
        description: error instanceof Error ? error.message : '无法上传或导入数据包',
        variant: 'destructive',
      })
    } finally {
      setImportUploading(false)
    }
  }, [importConfig, importData, importFile, importLogs, importPlugins, toast])

  return (
    <ScrollArea className="h-full">
      <div className="mx-auto w-full max-w-6xl space-y-6 p-4 sm:p-6">
        <div>
          <h1 className="text-2xl font-bold sm:text-3xl">数据迁移</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            导出 MaiBot 数据用于备份或迁移，也可以从此前导出的数据包恢复。
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Archive className="h-5 w-5" />
              MaiBot 数据导入导出
            </CardTitle>
            <CardDescription>config 与 data 默认包含，插件和日志可按需选择</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-6 xl:grid-cols-2">
              <div className="space-y-4 rounded-lg border p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <h2 className="font-medium">导出数据</h2>
                    <p className="text-muted-foreground mt-1 text-sm">
                      生成包含 manifest.json 的 zip 压缩包
                    </p>
                  </div>
                  <Button
                    className="gap-2 sm:w-auto"
                    disabled={exportCreating || isTransferJobRunning(exportJob)}
                    onClick={() => void handleCreateExport()}
                  >
                    {exportCreating || isTransferJobRunning(exportJob) ? (
                      <RefreshCw className="h-4 w-4 animate-spin" />
                    ) : (
                      <Download className="h-4 w-4" />
                    )}
                    开始导出
                  </Button>
                </div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <label
                    htmlFor="export-core-data"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox id="export-core-data" checked disabled className="mt-0.5" />
                    <span>
                      <span className="block text-sm font-medium">配置与数据</span>
                      <span className="text-muted-foreground block text-xs">config / data</span>
                    </span>
                  </label>
                  <label
                    htmlFor="export-plugins"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      id="export-plugins"
                      checked={exportIncludePlugins}
                      onCheckedChange={(value) => setExportIncludePlugins(value === true)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium">已安装插件</span>
                      <span className="text-muted-foreground block text-xs">plugins</span>
                    </span>
                  </label>
                  <label
                    htmlFor="export-logs"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      id="export-logs"
                      checked={exportIncludeLogs}
                      onCheckedChange={(value) => setExportIncludeLogs(value === true)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium">日志</span>
                      <span className="text-muted-foreground block text-xs">logs</span>
                    </span>
                  </label>
                </div>
                {exportJob && (
                  <div className="bg-muted/40 space-y-3 rounded-md p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-medium">{exportJob.message}</div>
                        <div className="text-muted-foreground text-xs">
                          {exportJob.processed_files}/{exportJob.total_files} 个文件 ·{' '}
                          {formatStorageBytes(exportJob.processed_bytes)}/
                          {formatStorageBytes(exportJob.total_bytes)}
                        </div>
                      </div>
                      <Badge variant={exportJob.status === 'failed' ? 'destructive' : 'secondary'}>
                        {exportJob.status}
                      </Badge>
                    </div>
                    <Progress value={exportJob.progress} className="h-2" />
                    {exportJob.error && (
                      <p className="text-destructive text-sm">{exportJob.error}</p>
                    )}
                    {isTransferJobRunning(exportJob) && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        onClick={() => void handleCancelExport()}
                      >
                        <X className="h-4 w-4" />
                        取消导出
                      </Button>
                    )}
                    {exportJob.status === 'completed' && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="gap-2"
                        onClick={() => void handleDownloadExport()}
                      >
                        <Download className="h-4 w-4" />
                        下载压缩包
                      </Button>
                    )}
                  </div>
                )}
              </div>

              <div className="space-y-4 rounded-lg border p-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <h2 className="font-medium">导入数据</h2>
                    <p className="text-muted-foreground mt-1 text-sm">
                      支持由本功能导出的 MaiBot 数据包
                    </p>
                  </div>
                  <Button
                    className="gap-2 sm:w-auto"
                    disabled={importUploading || isTransferJobRunning(importJob)}
                    onClick={() => void handleCreateImport()}
                  >
                    {importUploading || isTransferJobRunning(importJob) ? (
                      <RefreshCw className="h-4 w-4 animate-spin" />
                    ) : (
                      <Upload className="h-4 w-4" />
                    )}
                    开始导入
                  </Button>
                </div>
                <Input
                  type="file"
                  accept=".zip,application/zip"
                  onChange={(event) => setImportFile(event.target.files?.[0] ?? null)}
                />
                <div className="grid gap-3 sm:grid-cols-2">
                  <label
                    htmlFor="import-config"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      id="import-config"
                      checked={importConfig}
                      onCheckedChange={(value) => setImportConfig(value === true)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium">配置</span>
                      <span className="text-muted-foreground block text-xs">config</span>
                    </span>
                  </label>
                  <label
                    htmlFor="import-data"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      id="import-data"
                      checked={importData}
                      onCheckedChange={(value) => setImportData(value === true)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium">数据</span>
                      <span className="text-muted-foreground block text-xs">data</span>
                    </span>
                  </label>
                  <label
                    htmlFor="import-plugins"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      id="import-plugins"
                      checked={importPlugins}
                      onCheckedChange={(value) => setImportPlugins(value === true)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium">插件</span>
                      <span className="text-muted-foreground block text-xs">plugins</span>
                    </span>
                  </label>
                  <label
                    htmlFor="import-logs"
                    className="flex cursor-pointer items-start gap-3 rounded-md border p-3"
                  >
                    <Checkbox
                      id="import-logs"
                      checked={importLogs}
                      onCheckedChange={(value) => setImportLogs(value === true)}
                      className="mt-0.5"
                    />
                    <span>
                      <span className="block text-sm font-medium">日志</span>
                      <span className="text-muted-foreground block text-xs">logs</span>
                    </span>
                  </label>
                </div>
                {(importUploading || importJob) && (
                  <div className="bg-muted/40 space-y-3 rounded-md p-3">
                    {importUploading && !importJob ? (
                      <>
                        <div className="flex items-center justify-between gap-2 text-sm">
                          <span className="font-medium">正在上传数据包</span>
                          <span className="text-muted-foreground">{importUploadProgress}%</span>
                        </div>
                        <Progress value={importUploadProgress} className="h-2" />
                      </>
                    ) : importJob ? (
                      <>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="min-w-0">
                            <div className="text-sm font-medium">{importJob.message}</div>
                            <div className="text-muted-foreground text-xs">
                              {importJob.processed_files}/{importJob.total_files} 个文件 ·{' '}
                              {formatStorageBytes(importJob.processed_bytes)}/
                              {formatStorageBytes(importJob.total_bytes)}
                            </div>
                          </div>
                          <Badge
                            variant={importJob.status === 'failed' ? 'destructive' : 'secondary'}
                          >
                            {importJob.status}
                          </Badge>
                        </div>
                        <Progress value={importJob.progress} className="h-2" />
                        {importJob.error && (
                          <p className="text-destructive text-sm">{importJob.error}</p>
                        )}
                      </>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </ScrollArea>
  )
}
