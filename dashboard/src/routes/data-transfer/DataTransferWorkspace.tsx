import { Box, Download, RefreshCw, Trash2, Upload, X } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { FileDropzone, ResourceRow, SectionPanel, SelectionCard } from '@/components/ui/management'
import { Separator } from '@/components/ui/separator'
import { DataPanel } from '@/components/ui/surface'
import type { DataExportOptions, DataImportOptions } from '@/lib/data-transfer-api'
import { formatStorageBytes } from '@/lib/storage-size'
import { TransferJobProgress } from '@/routes/data-transfer/TransferJobProgress'
import {
  isTransferJobRunning,
  type DataTransferViewModel,
} from '@/routes/data-transfer/useDataTransfer'

const exportOptions: {
  key: keyof DataExportOptions
  id: string
  label: string
  description: string
}[] = [
  { key: 'include_plugins', id: 'export-plugins', label: '已安装插件', description: 'plugins' },
  { key: 'include_logs', id: 'export-logs', label: '日志', description: 'logs' },
]
const importOptions: {
  key: keyof DataImportOptions
  id: string
  label: string
  description: string
}[] = [
  { key: 'import_config', id: 'import-config', label: '配置', description: 'config' },
  { key: 'import_data', id: 'import-data', label: '数据', description: 'data' },
  { key: 'import_plugins', id: 'import-plugins', label: '插件', description: 'plugins' },
  { key: 'import_logs', id: 'import-logs', label: '日志', description: 'logs' },
]

/** 生产页面和设计预览使用同一棵展示组件，数据来源由外部提供。 */
export function DataTransferWorkspace({ viewModel: model }: { viewModel: DataTransferViewModel }) {
  const exporting = model.exportCreating || isTransferJobRunning(model.exportJob)
  const importing = model.importUploading || isTransferJobRunning(model.importJob)
  const exportSelectedCount = 1 + Object.values(model.exportOptions).filter(Boolean).length
  const importSelectedCount = Object.values(model.importOptions).filter(Boolean).length

  return (
    <div className="space-y-5">
      <header className="space-y-1">
        <h1 className="text-2xl font-normal">数据导入导出</h1>
        <p className="text-muted-foreground text-sm">
          备份或迁移 MaiBot：config 与 data 默认包含，插件和日志按需选择
        </p>
      </header>
      <div className="grid items-stretch gap-5 lg:grid-cols-2">
        <SectionPanel
          title="导出"
          description="生成带 manifest.json 的 zip 包"
          leading={<Badge className="text-xs font-normal">01</Badge>}
          bodyClassName="flex flex-col gap-4 p-4 sm:p-5"
        >
          <fieldset className="space-y-2" disabled={exporting}>
            <legend className="text-muted-foreground mb-2 text-xs">打包内容</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              <SelectionCard
                id="export-core-data"
                label="配置与数据"
                description="config / data"
                checked
                required
              />
              {exportOptions.map((option) => (
                <SelectionCard
                  key={option.key}
                  id={option.id}
                  label={option.label}
                  description={option.description}
                  checked={model.exportOptions[option.key]}
                  disabled={exporting}
                  onCheckedChange={(value) => model.setExportOption(option.key, value)}
                />
              ))}
            </div>
          </fieldset>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-muted-foreground text-xs">已选 {exportSelectedCount} 项</span>
            <Button disabled={exporting} onClick={() => void model.createExport()}>
              {exporting ? <RefreshCw className="animate-spin" /> : <Download />}
              开始导出
            </Button>
          </div>
          <TransferJobProgress
            job={model.exportJob}
            actions={
              isTransferJobRunning(model.exportJob) ? (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={model.exportCancelling}
                  onClick={() => void model.cancelExport()}
                >
                  <X />
                  取消导出
                </Button>
              ) : model.exportJob?.status === 'completed' && model.exportJob.download_url ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    if (model.exportJob) void model.downloadExport(model.exportJob)
                  }}
                >
                  <Download />
                  下载压缩包
                </Button>
              ) : undefined
            }
          />
          <Separator variant="groove" />
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">导出历史</h3>
            <Button
              variant="ghost"
              size="xs"
              disabled={model.historyLoading}
              onClick={() => void model.refreshHistory()}
            >
              <RefreshCw className={model.historyLoading ? 'animate-spin' : undefined} />
              刷新
            </Button>
          </div>
          {model.historyError && (
            <p className="text-destructive text-sm" role="alert">
              {model.historyError}
            </p>
          )}
          <DataPanel padding="none" className="mt-auto">
            {model.exportHistory.length === 0 ? (
              <div className="flex min-h-32 flex-col items-center justify-center gap-2 p-4 text-center">
                <Box className="text-muted-foreground size-6" />
                <p className="text-sm font-medium">
                  {model.historyLoading
                    ? '正在加载导出记录'
                    : model.historyError
                      ? '导出记录加载失败'
                      : '还没有导出记录'}
                </p>
                <p className="text-muted-foreground text-xs">压缩包保留 24 小时，下载后重新计时</p>
              </div>
            ) : (
              <div>
                <p className="text-muted-foreground px-4 pt-3 text-xs">
                  压缩包保留 24 小时，下载后重新计时
                </p>
                {model.exportHistory.map((job) => (
                  <ResourceRow
                    key={job.job_id}
                    title={<span className="text-sm break-all">{job.filename}</span>}
                    description={
                      <span className="space-y-1 text-xs">
                        <span className="block">
                          {formatStorageBytes(job.archive_bytes ?? 0)}
                          {job.completed_at != null &&
                            ` · 完成于 ${new Date(job.completed_at * 1000).toLocaleString()}`}
                        </span>
                        <span className="block">
                          {job.download_url && job.expires_at != null
                            ? `保留至 ${new Date(job.expires_at * 1000).toLocaleString()}`
                            : '文件已过期或已删除'}
                        </span>
                      </span>
                    }
                    actions={
                      <>
                        <Button
                          variant="outline"
                          size="icon-xs"
                          aria-label={`下载 ${job.filename}`}
                          title="下载"
                          disabled={!job.download_url}
                          onClick={() => void model.downloadExport(job)}
                        >
                          <Download />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-label={`删除 ${job.filename}`}
                          title="删除"
                          onClick={() => void model.deleteExport(job)}
                        >
                          <Trash2 />
                        </Button>
                      </>
                    }
                  />
                ))}
              </div>
            )}
          </DataPanel>
        </SectionPanel>
        <SectionPanel
          title="导入"
          description="仅支持本功能导出的数据包"
          leading={
            <Badge variant="success" className="text-xs font-normal">
              02
            </Badge>
          }
          bodyClassName="flex flex-col gap-4 p-4 sm:p-5"
        >
          <FileDropzone
            file={model.importFile}
            onFileChange={model.setImportFile}
            disabled={importing}
            accept=".zip,application/zip"
            label="拖入 .zip 数据包，或点击选择"
            description="选择文件后再勾选要导入的内容"
            onError={model.reportFileError}
          />
          <fieldset className="space-y-2" disabled={!model.importFile || importing}>
            <legend className="text-muted-foreground mb-2 text-xs">导入内容</legend>
            <div className="grid gap-3 sm:grid-cols-2">
              {importOptions.map((option) => (
                <SelectionCard
                  key={option.key}
                  id={option.id}
                  label={option.label}
                  description={option.description}
                  checked={model.importOptions[option.key]}
                  disabled={!model.importFile || importing}
                  onCheckedChange={(value) => model.setImportOption(option.key, value)}
                />
              ))}
            </div>
          </fieldset>
          <TransferJobProgress
            job={model.importJob}
            uploadProgress={model.importUploading ? model.importUploadProgress : undefined}
          />
          <div className="mt-auto flex flex-wrap items-center justify-between gap-3 pt-5">
            <span className="text-muted-foreground text-xs">
              {!model.importFile
                ? '先选择数据包'
                : importSelectedCount === 0
                  ? '请至少选择一个导入范围'
                  : `已选 ${importSelectedCount} 项`}
            </span>
            <Button
              disabled={!model.importFile || importing || importSelectedCount === 0}
              onClick={() => void model.createImport()}
            >
              {importing ? <RefreshCw className="animate-spin" /> : <Upload />}
              开始导入
            </Button>
          </div>
        </SectionPanel>
      </div>
    </div>
  )
}
