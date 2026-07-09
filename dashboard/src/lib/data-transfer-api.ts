import { resolveApiPath } from '@/lib/api-base'
import { backendApi } from '@/lib/http'

export type DataTransferJobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export interface DataTransferJob {
  job_id: string
  kind: 'export' | 'import'
  status: DataTransferJobStatus
  progress: number
  message: string
  total_files: number
  processed_files: number
  total_bytes: number
  processed_bytes: number
  filename: string | null
  download_url: string | null
  manifest: Record<string, unknown> | null
  error: string | null
}

export interface DataExportOptions {
  include_plugins: boolean
  include_logs: boolean
}

export interface DataImportOptions {
  import_config: boolean
  import_data: boolean
  import_plugins: boolean
  import_logs: boolean
}

export async function createDataExportJob(options: DataExportOptions): Promise<DataTransferJob> {
  return backendApi.post<DataTransferJob>('/api/webui/data-transfer/export', {
    body: options,
    errorMessage: '创建导出任务失败',
  })
}

export async function getDataTransferJob(jobId: string): Promise<DataTransferJob> {
  return backendApi.get<DataTransferJob>(`/api/webui/data-transfer/jobs/${encodeURIComponent(jobId)}`, {
    cache: 'no-store',
    errorMessage: '获取数据迁移任务进度失败',
  })
}

export async function downloadDataExport(job: DataTransferJob): Promise<void> {
  if (!job.download_url) {
    throw new Error('导出任务还没有可下载文件')
  }
  const blob = await backendApi.get<Blob>(job.download_url, {
    parse: 'blob',
    cache: 'no-store',
    errorMessage: '下载导出文件失败',
  })
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = job.filename || 'maibot-data.zip'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(objectUrl)
}

export async function cancelDataExportJob(jobId: string): Promise<DataTransferJob> {
  return backendApi.post<DataTransferJob>(
    `/api/webui/data-transfer/export/${encodeURIComponent(jobId)}/cancel`,
    {
      errorMessage: '取消导出任务失败',
    }
  )
}

export async function createDataImportJob(
  file: File,
  options: DataImportOptions,
  onUploadProgress?: (progress: number) => void
): Promise<{ job_id: string; status: DataTransferJobStatus }> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('import_config', String(options.import_config))
  formData.append('import_data', String(options.import_data))
  formData.append('import_plugins', String(options.import_plugins))
  formData.append('import_logs', String(options.import_logs))

  const url = await resolveApiPath('/api/webui/data-transfer/import')
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', url)
    request.withCredentials = true
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onUploadProgress?.(Math.round((event.loaded / event.total) * 100))
      }
    }
    request.onload = () => {
      let payload: unknown = null
      try {
        payload = request.responseText ? JSON.parse(request.responseText) : null
      } catch {
        payload = request.responseText
      }

      if (request.status >= 200 && request.status < 300) {
        resolve(payload as { job_id: string; status: DataTransferJobStatus })
        return
      }

      const detail =
        payload && typeof payload === 'object' && 'detail' in payload
          ? String((payload as { detail: unknown }).detail)
          : request.statusText || '上传导入包失败'
      reject(new Error(detail))
    }
    request.onerror = () => reject(new Error('网络请求失败：无法上传导入包'))
    request.send(formData)
  })
}
