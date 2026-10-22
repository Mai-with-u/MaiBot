import type { ReactNode } from 'react'

import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { DataPanel } from '@/components/ui/surface'
import type { DataTransferJob } from '@/lib/data-transfer-api'
import { formatStorageBytes } from '@/lib/storage-size'

export function TransferJobProgress({
  job,
  uploadProgress,
  actions,
}: {
  job: DataTransferJob | null
  uploadProgress?: number
  actions?: ReactNode
}) {
  if (uploadProgress === undefined && !job) return null
  return (
    <DataPanel className="space-y-3" role="status" aria-live="polite">
      {uploadProgress !== undefined ? (
        <>
          <div className="flex items-center justify-between gap-2 text-sm">
            <span className="font-medium">正在上传数据包</span>
            <span className="text-muted-foreground">{uploadProgress}%</span>
          </div>
          <Progress value={uploadProgress} aria-label="数据包上传进度" />
        </>
      ) : job ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0 space-y-1">
              <div className="text-sm font-medium">{job.message}</div>
              <div className="text-muted-foreground text-xs">
                {job.processed_files}/{job.total_files} 个文件 ·{' '}
                {formatStorageBytes(job.processed_bytes)}/{formatStorageBytes(job.total_bytes)}
              </div>
            </div>
            <Badge variant={job.status === 'failed' ? 'destructive' : 'secondary'}>
              {job.status}
            </Badge>
          </div>
          <Progress
            value={job.progress}
            aria-label={job.kind === 'export' ? '导出进度' : '导入进度'}
          />
          {job.error && <p className="text-destructive text-sm">{job.error}</p>}
          {actions}
        </>
      ) : null}
    </DataPanel>
  )
}
