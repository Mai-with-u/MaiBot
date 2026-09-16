import type { MemoryImportInputMode, MemoryImportTaskKind } from '@/lib/memory-api'

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

export const IMPORT_TASK_KIND_TEXT: Record<string, string> = {
  upload: '资料导入',
  paste: '粘贴导入',
  raw_scan: '本地扫描',
  lpmm_openie: 'LPMM OpenIE',
  lpmm_convert: 'LPMM 转换',
  temporal_backfill: '时间回填',
  maibot_migration: 'Maibot 迁移',
}

/** 记忆来源类别 → 展示标签；类别由后端按来源前缀解析后回填 source_kind */
export const MEMORY_SOURCE_KIND_TEXT: Record<string, string> = {
  chat_summary: '聊天摘要',
  chat_stream: '聊天流',
  chat_history: '聊天记录',
  person_fact: '人物事实',
}

/**
 * 单独占一个筛选标签页的来源类别。
 * 其余类别（聊天流、聊天记录等）与无法识别的来源统一归入「其他」，
 * 行尾徽章仍按 MEMORY_SOURCE_KIND_TEXT 标出具体类别，信息不丢失。
 */
export const MEMORY_SOURCE_KIND_FILTER_KINDS = ['chat_summary', 'person_fact'] as const

/** 来源类别筛选的「全部」取值 */
export const MEMORY_SOURCE_KIND_FILTER_ALL = 'all'

/** 来源类别筛选的「其他」取值：兜住没有独立标签页的类别与解析不出类别的来源 */
export const MEMORY_SOURCE_KIND_FILTER_OTHER = 'other'

export const IMPORT_KIND_OPTIONS: Array<{
  value: MemoryImportTaskKind
  label: string
  description: string
}> = [
  { value: 'upload', label: '资料导入', description: '导入文本、文件或文件夹' },
  { value: 'lpmm_openie', label: 'LPMM OpenIE', description: '读取 LPMM 数据并抽取关系' },
  { value: 'lpmm_convert', label: 'LPMM 转换', description: '将 LPMM 数据转换到目标目录' },
]

/**
 * 资料类别选项：description 说明该类别的切块与抽取方式，创建任务时悬停可看。
 * value 与 useImportForm 的 ImportContentCategory 一致（此处用字符串避免常量层反向依赖 hook）。
 */
export const IMPORT_CONTENT_CATEGORY_OPTIONS: Array<{
  value: string
  label: string
  description: string
}> = [
  {
    value: 'narrative',
    label: '叙事资料',
    description:
      '适合小说、剧情、长对话等连续叙事。先按标题/分隔符切成场景，再滑动窗口切块（默认 1600 字窗口、400 字重叠），抽取事件与人物关系。',
  },
  {
    value: 'factual',
    label: '事实资料',
    description:
      '适合设定集、术语表、说明文档、条目清单。按结构切块（默认 1200 字，不拆散列表、定义与表格），抽取事实三元组与实体。',
  },
  {
    value: 'quote',
    label: '语录与短句',
    description: '适合语录、金句、短文本。整段作为语录保留，不做关系抽取。',
  },
  {
    value: 'chat_log',
    label: '聊天记录',
    description:
      '适合导出的聊天记录。按消息边界切块，超长消息保留为完整一条并给出提示，窗口参数与叙事资料一致。',
  },
]

/** 输入模式选项：description 说明文本与结构化 JSON 的解析差异，悬停可看 */
export const IMPORT_INPUT_MODE_OPTIONS: Array<{
  value: MemoryImportInputMode
  label: string
  description: string
}> = [
  { value: 'text', label: '文本', description: '按纯文本解析，整段内容当作正文切块与抽取。' },
  {
    value: 'json',
    label: '结构化 JSON',
    description: '按 JSON 结构解析，保留字段层级与键名，适合已有结构化数据。',
  },
]
