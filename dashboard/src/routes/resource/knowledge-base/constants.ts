import type { MemoryImportTaskKind } from '@/lib/memory-api'

export const DELETE_OPERATION_FETCH_LIMIT = 100
export const DELETE_OPERATION_PAGE_SIZE = 6
export const DELETE_OPERATION_ITEM_PAGE_SIZE = 8
export const FEEDBACK_CORRECTION_FETCH_LIMIT = 100
export const FEEDBACK_CORRECTION_PAGE_SIZE = 6
export const FEEDBACK_ACTION_LOG_PAGE_SIZE = 8
export const MEMORY_CORRECTION_FETCH_LIMIT = 100
export const MEMORY_CORRECTION_PAGE_SIZE = 6
export const IMPORT_CHUNK_PAGE_SIZE = 50

export const RUNNING_IMPORT_STATUS = new Set(['preparing', 'running', 'cancel_requested'])
export const QUEUED_IMPORT_STATUS = new Set(['queued'])

export const IMPORT_STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  preparing: '准备中',
  running: '运行中',
  cancel_requested: '取消中',
  cancelled: '已取消',
  completed: '已完成',
  completed_with_errors: '完成（有错误）',
  failed: '失败',
}

export const IMPORT_STEP_TEXT: Record<string, string> = {
  queued: '排队中',
  preparing: '准备中',
  running: '运行中',
  splitting: '分块中',
  extracting: '抽取中',
  writing: '写入中',
  saving: '保存中',
  backfilling: '回填中',
  converting: '转换中',
  verifying: '校验中',
  switching: '切换中',
  cancel_requested: '取消中',
  cancelled: '已取消',
  completed: '已完成',
  completed_with_errors: '完成（有错误）',
  failed: '失败',
}

export const IMPORT_KIND_OPTIONS: Array<{
  value: MemoryImportTaskKind
  label: string
  description: string
}> = [
  { value: 'upload', label: '资料导入', description: '导入文本、文件或文件夹' },
  { value: 'lpmm_openie', label: 'LPMM OpenIE', description: '读取 LPMM 数据并抽取关系' },
  { value: 'lpmm_convert', label: 'LPMM 转换', description: '将 LPMM 数据转换到目标目录' },
]
