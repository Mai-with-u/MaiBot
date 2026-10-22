import { useCallback, useEffect, useRef, useState } from 'react'

import { useToast } from '@/hooks/use-toast'
import {
  cancelDataExportJob,
  createDataExportJob,
  createDataImportJob,
  deleteDataExport,
  downloadDataExport,
  getDataExportHistory,
  getDataTransferJob,
  type DataExportOptions,
  type DataImportOptions,
  type DataTransferJob,
} from '@/lib/data-transfer-api'

export interface DataTransferViewModel {
  exportOptions: DataExportOptions
  setExportOption: (key: keyof DataExportOptions, checked: boolean) => void
  exportJob: DataTransferJob | null
  exportCreating: boolean
  exportCancelling: boolean
  exportHistory: DataTransferJob[]
  historyError: string | null
  historyLoading: boolean
  importFile: File | null
  setImportFile: (file: File | null) => void
  importOptions: DataImportOptions
  setImportOption: (key: keyof DataImportOptions, checked: boolean) => void
  importJob: DataTransferJob | null
  importUploading: boolean
  importUploadProgress: number
  createExport: () => void | Promise<void>
  cancelExport: () => void | Promise<void>
  refreshHistory: () => void | Promise<void>
  downloadExport: (job: DataTransferJob) => void | Promise<void>
  deleteExport: (job: DataTransferJob) => void | Promise<void>
  createImport: () => void | Promise<void>
  reportFileError: (message: string) => void
}

export function isTransferJobRunning(
  job: DataTransferJob | null
): job is DataTransferJob & { status: 'pending' | 'running' } {
  return job?.status === 'pending' || job?.status === 'running'
}

/** 每个任务只保留一个请求；取消、换任务或离开页面后，旧请求不再写回状态。 */
function useTransferJobPolling(
  job: DataTransferJob | null,
  onUpdate: (job: DataTransferJob) => void,
  onError: (error: unknown) => void,
  paused = false
) {
  const jobId = job?.job_id
  const running = isTransferJobRunning(job)
  useEffect(() => {
    if (!jobId || !running || paused) return
    let cancelled = false
    let timerId: number
    const poll = async () => {
      let continuePolling = true
      try {
        const latest = await getDataTransferJob(jobId)
        if (cancelled) return
        onUpdate(latest)
        continuePolling = isTransferJobRunning(latest)
      } catch (error) {
        if (cancelled) return
        onError(error)
      }
      if (!cancelled && continuePolling) timerId = window.setTimeout(() => void poll(), 1200)
    }
    timerId = window.setTimeout(() => void poll(), 1200)
    return () => {
      cancelled = true
      window.clearTimeout(timerId)
    }
  }, [jobId, running, paused, onUpdate, onError])
}

export function useDataTransfer(): DataTransferViewModel {
  const { toast } = useToast()
  const mounted = useRef(false)
  const historyRequest = useRef(0)
  const [exportOptions, setExportOptions] = useState<DataExportOptions>({
    include_plugins: false,
    include_logs: false,
  })
  const [exportJob, setExportJob] = useState<DataTransferJob | null>(null)
  const [exportCreating, setExportCreating] = useState(false)
  const [exportCancelling, setExportCancelling] = useState(false)
  const [exportHistory, setExportHistory] = useState<DataTransferJob[]>([])
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importOptions, setImportOptions] = useState<DataImportOptions>({
    import_config: true,
    import_data: true,
    import_plugins: false,
    import_logs: false,
  })
  const [importJob, setImportJob] = useState<DataTransferJob | null>(null)
  const [importUploading, setImportUploading] = useState(false)
  const [importUploadProgress, setImportUploadProgress] = useState(0)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])

  const refreshHistory = useCallback(async () => {
    const request = ++historyRequest.current
    setHistoryLoading(true)
    try {
      const history = await getDataExportHistory()
      if (!mounted.current || request !== historyRequest.current) return
      setExportHistory(history)
      setHistoryError(null)
    } catch (error) {
      if (!mounted.current || request !== historyRequest.current) return
      setHistoryError(error instanceof Error ? error.message : '获取导出历史失败')
    } finally {
      if (mounted.current && request === historyRequest.current) setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    void refreshHistory()
  }, [refreshHistory, exportJob?.status])

  const reportJobFailure = useCallback(
    (job: DataTransferJob) => {
      if (job.status === 'failed')
        toast({
          title: job.kind === 'export' ? '导出失败' : '导入失败',
          description: job.error || job.message,
          variant: 'destructive',
        })
    },
    [toast]
  )
  const updateExportJob = useCallback(
    (job: DataTransferJob) => {
      setExportJob(job)
      reportJobFailure(job)
    },
    [reportJobFailure]
  )
  const updateImportJob = useCallback(
    (job: DataTransferJob) => {
      setImportJob(job)
      reportJobFailure(job)
    },
    [reportJobFailure]
  )
  const reportPollingError = useCallback(
    (error: unknown) => {
      toast({
        title: '任务进度刷新失败',
        description: error instanceof Error ? error.message : '无法读取数据迁移任务状态',
        variant: 'destructive',
      })
    },
    [toast]
  )
  useTransferJobPolling(exportJob, updateExportJob, reportPollingError, exportCancelling)
  useTransferJobPolling(importJob, updateImportJob, reportPollingError, importUploading)

  const createExport = async () => {
    if (exportCreating || isTransferJobRunning(exportJob)) return
    setExportCreating(true)
    try {
      const job = await createDataExportJob(exportOptions)
      if (!mounted.current) return
      setExportJob(job)
      toast({ title: '已开始导出 MaiBot 数据' })
    } catch (error) {
      if (mounted.current)
        toast({
          title: '创建导出任务失败',
          description: error instanceof Error ? error.message : '无法创建导出任务',
          variant: 'destructive',
        })
    } finally {
      if (mounted.current) setExportCreating(false)
    }
  }

  const cancelExport = async () => {
    if (!isTransferJobRunning(exportJob) || exportCancelling) return
    // 暂停轮询，使取消前发出的进度请求不能覆盖取消结果。
    setExportCancelling(true)
    try {
      const job = await cancelDataExportJob(exportJob.job_id)
      if (!mounted.current) return
      setExportJob(job)
      toast({ title: '正在取消导出' })
    } catch (error) {
      if (mounted.current)
        toast({
          title: '取消导出失败',
          description: error instanceof Error ? error.message : '无法取消当前导出任务',
          variant: 'destructive',
        })
    } finally {
      if (mounted.current) setExportCancelling(false)
    }
  }

  const downloadExport = async (job: DataTransferJob) => {
    try {
      await downloadDataExport(job)
      // 下载会延长服务端保留时间，刷新后展示新的到期时间。
      if (mounted.current) await refreshHistory()
    } catch (error) {
      if (mounted.current)
        toast({
          title: '下载失败',
          description: error instanceof Error ? error.message : '无法下载导出文件',
          variant: 'destructive',
        })
    }
  }

  const deleteExport = async (job: DataTransferJob) => {
    try {
      await deleteDataExport(job.job_id)
      if (!mounted.current) return
      setExportJob((current) => (current?.job_id === job.job_id ? null : current))
      await refreshHistory()
      if (mounted.current) toast({ title: '已删除导出记录和压缩包' })
    } catch (error) {
      if (mounted.current)
        toast({
          title: '删除失败',
          description: error instanceof Error ? error.message : '无法删除',
          variant: 'destructive',
        })
    }
  }

  const createImport = async () => {
    if (importUploading || isTransferJobRunning(importJob)) return
    if (!importFile) {
      toast({ title: '请选择要导入的压缩包', variant: 'destructive' })
      return
    }
    if (!Object.values(importOptions).some(Boolean)) {
      toast({ title: '请至少选择一个导入范围', variant: 'destructive' })
      return
    }
    setImportJob(null)
    setImportUploading(true)
    setImportUploadProgress(0)
    try {
      const response = await createDataImportJob(importFile, importOptions, (progress) => {
        if (mounted.current) setImportUploadProgress(progress)
      })
      if (!mounted.current) return
      // 上传已创建任务。先记住任务 ID，即使首次读取状态失败也继续跟踪，避免误以为未导入而重复提交。
      const acceptedJob: DataTransferJob = {
        job_id: response.job_id,
        kind: 'import',
        status: response.status,
        progress: 0,
        message: '正在读取导入进度',
        total_files: 0,
        processed_files: 0,
        total_bytes: 0,
        processed_bytes: 0,
        filename: importFile.name,
        download_url: null,
        manifest: null,
        error: null,
      }
      setImportJob(acceptedJob)
      try {
        const job = await getDataTransferJob(response.job_id)
        if (!mounted.current) return
        setImportJob(job)
      } catch (error) {
        if (mounted.current) reportPollingError(error)
      }
      if (mounted.current) toast({ title: '已开始导入 MaiBot 数据' })
    } catch (error) {
      if (mounted.current)
        toast({
          title: '创建导入任务失败',
          description: error instanceof Error ? error.message : '无法上传或导入数据包',
          variant: 'destructive',
        })
    } finally {
      if (mounted.current) setImportUploading(false)
    }
  }

  return {
    exportOptions,
    setExportOption: (key, checked) =>
      setExportOptions((current) => ({ ...current, [key]: checked })),
    exportJob,
    exportCreating,
    exportCancelling,
    exportHistory,
    historyError,
    historyLoading,
    importFile,
    setImportFile,
    importOptions,
    setImportOption: (key, checked) =>
      setImportOptions((current) => ({ ...current, [key]: checked })),
    importJob,
    importUploading,
    importUploadProgress,
    createExport,
    cancelExport,
    refreshHistory,
    downloadExport,
    deleteExport,
    createImport,
    reportFileError: (message) =>
      toast({ title: '无法选择数据包', description: message, variant: 'destructive' }),
  }
}
