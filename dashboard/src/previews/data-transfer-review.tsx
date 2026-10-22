import { useState } from 'react'

import type { DataExportOptions, DataImportOptions, DataTransferJob } from '@/lib/data-transfer-api'
import { DataTransferWorkspace } from '@/routes/data-transfer/DataTransferWorkspace'
import type { DataTransferViewModel } from '@/routes/data-transfer/useDataTransfer'

/** 与生产页共用展示组件；演示任务只保存在内存，不读取、上传或打包业务数据。 */
export function DataTransferReview() {
  const [exportOptions, setExportOptions] = useState<DataExportOptions>({
    include_plugins: false,
    include_logs: false,
  })
  const [importOptions, setImportOptions] = useState<DataImportOptions>({
    import_config: true,
    import_data: true,
    import_plugins: false,
    import_logs: false,
  })
  const [importFile, setImportFile] = useState<File | null>(null)
  const [exportJob, setExportJob] = useState<DataTransferJob | null>(null)
  const [importJob, setImportJob] = useState<DataTransferJob | null>(null)
  const [history, setHistory] = useState<DataTransferJob[]>([])
  const [notice, setNotice] = useState('示例页面；选择文件不会上传，导入导出操作只演示界面状态。')

  const completeSampleJob = (kind: DataTransferJob['kind']): DataTransferJob => ({
    job_id: `${kind}-${Date.now()}`,
    kind,
    status: 'completed',
    progress: 100,
    message: kind === 'export' ? '示例导出完成' : '示例导入完成',
    total_files: 12,
    processed_files: 12,
    total_bytes: 2621440,
    processed_bytes: 2621440,
    filename: kind === 'export' ? 'maibot-data-example.zip' : (importFile?.name ?? null),
    download_url: kind === 'export' ? '#preview-download' : null,
    manifest: null,
    error: null,
    completed_at: Date.now() / 1000,
    expires_at: Date.now() / 1000 + 86400,
    archive_bytes: 2621440,
  })

  const model: DataTransferViewModel = {
    exportOptions,
    setExportOption: (key, value) => setExportOptions((current) => ({ ...current, [key]: value })),
    exportJob,
    exportCreating: false,
    exportCancelling: false,
    exportHistory: history,
    historyError: null,
    historyLoading: false,
    importFile,
    setImportFile: (file) => {
      setImportFile(file)
      setImportJob(null)
    },
    importOptions,
    setImportOption: (key, value) => setImportOptions((current) => ({ ...current, [key]: value })),
    importJob,
    importUploading: false,
    importUploadProgress: 0,
    createExport: () => {
      const job = completeSampleJob('export')
      setExportJob(job)
      setHistory((current) => [job, ...current])
      setNotice('示例导出已完成；未读取或打包本地数据。')
    },
    cancelExport: () => setNotice('示例页面没有正在执行的导出任务。'),
    refreshHistory: () => setNotice('已刷新本页示例历史。'),
    downloadExport: () => setNotice('预览不生成下载文件；正式页面会下载已经生成的数据包。'),
    deleteExport: (job) => {
      setHistory((current) => current.filter((item) => item.job_id !== job.job_id))
      if (exportJob?.job_id === job.job_id) setExportJob(null)
      setNotice('已移除本页示例记录。')
    },
    createImport: () => {
      setImportJob(completeSampleJob('import'))
      setNotice('示例导入已完成；未上传文件，也未修改本地数据。')
    },
    reportFileError: setNotice,
  }

  return (
    <main className="management-review-screen">
      <DataTransferWorkspace viewModel={model} />
      <p role="status" className="text-muted-foreground mt-6 text-xs">
        {notice}
      </p>
    </main>
  )
}
