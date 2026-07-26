import type {
  MemoryDeleteOperationPayload,
  MemoryFeedbackActionLogPayload,
  MemoryFeedbackCorrectionDetailTaskPayload,
  MemoryFeedbackCorrectionSummaryPayload,
  MemoryImportInputMode,
} from '@/lib/memory-api'

import {
  IMPORT_STATUS_TEXT,
  IMPORT_STEP_TEXT,
  QUEUED_IMPORT_STATUS,
  RUNNING_IMPORT_STATUS,
} from './constants'

export type DeleteOperationItem = NonNullable<MemoryDeleteOperationPayload['items']>[number]

export function normalizeProgress(value: number | string | null | undefined): number {
  const numeric = Number(value ?? 0)
  if (!Number.isFinite(numeric)) {
    return 0
  }
  const percent = numeric > 0 && numeric <= 1 ? numeric * 100 : numeric
  if (percent < 0) {
    return 0
  }
  if (percent > 100) {
    return 100
  }
  return percent
}

export function formatProgressPercent(value: number | string | null | undefined): string {
  return `${normalizeProgress(value).toFixed(1)}%`
}

export function parseOptionalPositiveInt(input: string): number | undefined {
  const value = input.trim()
  if (!value) {
    return undefined
  }
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return undefined
  }
  return parsed
}

export function parseOptionalNonNegativeInt(input: string): number | undefined {
  const value = input.trim()
  if (!value) {
    return undefined
  }
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed < 0) {
    return undefined
  }
  return parsed
}

export function parseCommaSeparatedList(input: string): string[] {
  return input
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

export function normalizeImportInputMode(value: string): MemoryImportInputMode {
  return value === 'json' ? 'json' : 'text'
}

export function getImportStatusLabel(status: string): string {
  const normalized = String(status ?? '').trim()
  if (!normalized) {
    return '-'
  }
  return IMPORT_STATUS_TEXT[normalized] ?? normalized
}

export function getImportStepLabel(step: string): string {
  const normalized = String(step ?? '').trim()
  if (!normalized) {
    return '-'
  }
  return IMPORT_STEP_TEXT[normalized] ?? normalized
}

export function getImportStatusVariant(
  status: string
): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'failed') {
    return 'destructive'
  }
  if (status === 'completed') {
    return 'default'
  }
  if (status === 'completed_with_errors' || status === 'cancelled') {
    return 'secondary'
  }
  if (RUNNING_IMPORT_STATUS.has(status) || QUEUED_IMPORT_STATUS.has(status)) {
    return 'outline'
  }
  return 'secondary'
}

export function formatImportTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '-'
  }
  const normalized = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000
  const value = new Date(normalized)
  if (Number.isNaN(value.getTime())) {
    return '-'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDeleteOperationMode(mode: string): string {
  switch (mode) {
    case 'entity':
      return '实体'
    case 'relation':
      return '关系'
    case 'paragraph':
      return '段落'
    case 'source':
      return '来源'
    case 'mixed':
      return '混合'
    default:
      return mode || '未知'
  }
}

export function formatDeleteOperationStatus(status: string): string {
  switch (status) {
    case 'executed':
      return '已执行'
    case 'restored':
      return '已恢复'
    default:
      return status || '未知'
  }
}

export function formatDeleteOperationTime(timestamp?: number | null): string {
  if (!timestamp) {
    return '未知时间'
  }
  const normalized = timestamp > 1_000_000_000_000 ? timestamp : timestamp * 1000
  const value = new Date(normalized)
  if (Number.isNaN(value.getTime())) {
    return '未知时间'
  }
  return value.toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function trimDeleteItemText(value: string, maxLength: number = 140): string {
  const normalized = String(value ?? '')
    .trim()
    .replace(/\s+/g, ' ')
  if (!normalized) {
    return ''
  }
  if (normalized.length <= maxLength) {
    return normalized
  }
  return `${normalized.slice(0, maxLength)}...`
}

export function formatDeleteRelationText(
  subject: string,
  predicate: string,
  object: string
): string {
  const left = String(subject ?? '').trim()
  const middle = String(predicate ?? '').trim()
  const right = String(object ?? '').trim()
  return [left, middle, right].filter(Boolean).join(' -> ')
}

export function getDeleteOperationItemLabel(item: DeleteOperationItem): string {
  const payload = item.payload ?? {}
  if (item.item_type === 'entity') {
    const entity = (payload.entity ?? {}) as Record<string, unknown>
    return String(entity.name ?? item.item_key ?? item.item_hash ?? '未命名实体')
  }
  if (item.item_type === 'relation') {
    const relation = (payload.relation ?? {}) as Record<string, unknown>
    return (
      formatDeleteRelationText(
        String(relation.subject ?? ''),
        String(relation.predicate ?? ''),
        String(relation.object ?? '')
      ) || String(item.item_key ?? item.item_hash ?? '未命名关系')
    )
  }
  if (item.item_type === 'paragraph') {
    const paragraph = (payload.paragraph ?? {}) as Record<string, unknown>
    const source = String(paragraph.source ?? '').trim()
    return source || String(item.item_key ?? item.item_hash ?? '未命名段落')
  }
  return String(item.item_key ?? item.item_hash ?? '未命名对象')
}

export function getDeleteOperationItemPreview(item: DeleteOperationItem): string {
  const payload = item.payload ?? {}
  if (item.item_type === 'entity') {
    const paragraphLinks = Array.isArray(payload.paragraph_links) ? payload.paragraph_links : []
    if (paragraphLinks.length > 0) {
      return `关联段落 ${paragraphLinks.length} 个`
    }
    return '实体快照'
  }
  if (item.item_type === 'relation') {
    const relation = (payload.relation ?? {}) as Record<string, unknown>
    const paragraphHashes = Array.isArray(payload.paragraph_hashes) ? payload.paragraph_hashes : []
    const { confidence } = relation
    const parts = []
    if (paragraphHashes.length > 0) {
      parts.push(`证据段落 ${paragraphHashes.length} 个`)
    }
    if (typeof confidence === 'number') {
      parts.push(`置信度 ${confidence.toFixed(2)}`)
    }
    return parts.join('，') || '关系快照'
  }
  if (item.item_type === 'paragraph') {
    const paragraph = (payload.paragraph ?? {}) as Record<string, unknown>
    return trimDeleteItemText(String(paragraph.content ?? ''))
  }
  return ''
}

export function getDeleteOperationItemSource(item: DeleteOperationItem): string {
  const payload = item.payload ?? {}
  if (item.item_type === 'paragraph') {
    const paragraph = (payload.paragraph ?? {}) as Record<string, unknown>
    return String(paragraph.source ?? '').trim()
  }
  return String(payload.source ?? '').trim()
}

export function formatFeedbackDecision(decision: string): string {
  switch (decision) {
    case 'correct':
      return '纠正'
    case 'reject':
      return '否定'
    case 'confirm':
      return '确认'
    case 'supplement':
      return '补充'
    case 'none':
      return '无动作'
    default:
      return decision || '未知'
  }
}

export function formatFeedbackTaskStatus(status: string): string {
  switch (status) {
    case 'pending':
      return '待处理'
    case 'running':
      return '处理中'
    case 'applied':
      return '已应用'
    case 'skipped':
      return '已跳过'
    case 'error':
      return '失败'
    default:
      return status || '未知'
  }
}

export function formatFeedbackRollbackStatus(status: string): string {
  switch (status) {
    case 'none':
      return '未回退'
    case 'running':
      return '回退中'
    case 'rolled_back':
      return '已回退'
    case 'error':
      return '回退失败'
    default:
      return status || '未知'
  }
}

export function getFeedbackStatusVariant(
  status: string
): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'applied' || status === 'rolled_back') {
    return 'default'
  }
  if (status === 'error') {
    return 'destructive'
  }
  if (status === 'running' || status === 'pending') {
    return 'outline'
  }
  return 'secondary'
}

export function summarizeFeedbackActionPayload(value: Record<string, unknown> | undefined): string {
  if (!value) {
    return ''
  }
  const hash = String(value.hash ?? '').trim()
  const subject = String(value.subject ?? '').trim()
  const predicate = String(value.predicate ?? '').trim()
  const object = String(value.object ?? '').trim()
  if (subject && predicate && object) {
    return formatDeleteRelationText(subject, predicate, object)
  }
  if (hash) {
    return hash
  }
  if (Array.isArray(value.target_hashes) && value.target_hashes.length > 0) {
    return `targets ${value.target_hashes.length}`
  }
  return trimDeleteItemText(JSON.stringify(value, null, 2), 120)
}

export function pickFeedbackRelationTriplet(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object') {
    return null
  }
  const record = value as Record<string, unknown>
  const subject = String(record.subject ?? '').trim()
  const predicate = String(record.predicate ?? '').trim()
  const object = String(record.object ?? '').trim()
  if (!subject || !predicate || !object) {
    return null
  }
  return record
}

export function formatFeedbackRelationTriplet(value: unknown): string {
  const triplet = pickFeedbackRelationTriplet(value)
  if (!triplet) {
    return ''
  }
  return formatDeleteRelationText(
    String(triplet.subject ?? ''),
    String(triplet.predicate ?? ''),
    String(triplet.object ?? '')
  )
}

export function getFeedbackCorrectionPreview(
  task: MemoryFeedbackCorrectionDetailTaskPayload | MemoryFeedbackCorrectionSummaryPayload | null
): {
  headline: string
  oldRelation: string
  newRelation: string
} {
  if (!task) {
    return {
      headline: '当前没有纠错摘要',
      oldRelation: '',
      newRelation: '',
    }
  }

  const detailTask = task as MemoryFeedbackCorrectionDetailTaskPayload
  const rollbackPlanSummary = detailTask.rollback_plan_summary ?? {}
  const forgottenRelations = Array.isArray(rollbackPlanSummary.forgotten_relations)
    ? rollbackPlanSummary.forgotten_relations
    : []
  const correctedWrite =
    rollbackPlanSummary.corrected_write && typeof rollbackPlanSummary.corrected_write === 'object'
      ? rollbackPlanSummary.corrected_write
      : {}
  const correctedRelations = Array.isArray(
    (correctedWrite as Record<string, unknown>).corrected_relations
  )
    ? ((correctedWrite as Record<string, unknown>).corrected_relations as unknown[])
    : []

  const oldRelation = formatFeedbackRelationTriplet(forgottenRelations[0])
  const newRelation = formatFeedbackRelationTriplet(correctedRelations[0])

  if (oldRelation && newRelation) {
    return {
      headline: `将“${oldRelation}”纠正为“${newRelation}”`,
      oldRelation,
      newRelation,
    }
  }
  if (newRelation) {
    return {
      headline: `补充了新的纠错结论：“${newRelation}”`,
      oldRelation: '',
      newRelation,
    }
  }
  if (oldRelation) {
    return {
      headline: `撤销了旧记忆关系：“${oldRelation}”`,
      oldRelation,
      newRelation: '',
    }
  }
  return {
    headline: task.query_text || '当前纠错没有可读摘要',
    oldRelation: '',
    newRelation: '',
  }
}

export function buildFeedbackImpactSummary(
  task: MemoryFeedbackCorrectionDetailTaskPayload | MemoryFeedbackCorrectionSummaryPayload | null
): string[] {
  if (!task) {
    return []
  }

  const counts = task.affected_counts ?? {}
  const items: string[] = []
  if (Number(counts.relations ?? 0) > 0) {
    items.push(`影响关系 ${Number(counts.relations ?? 0)} 条`)
  }
  if (Number(counts.corrected_relations ?? 0) > 0) {
    items.push(`新增纠正关系 ${Number(counts.corrected_relations ?? 0)} 条`)
  }
  if (Number(counts.correction_paragraphs ?? 0) > 0) {
    items.push(`写入纠错段落 ${Number(counts.correction_paragraphs ?? 0)} 条`)
  }
  if (Number(counts.stale_paragraphs ?? 0) > 0) {
    items.push(`标记旧段落 ${Number(counts.stale_paragraphs ?? 0)} 条`)
  }
  if (Number(counts.episode_sources ?? 0) > 0) {
    items.push(`触发 Episode 修复 ${Number(counts.episode_sources ?? 0)} 个来源`)
  }
  if (Number(counts.profile_person_ids ?? 0) > 0) {
    items.push(`触发 Profile 刷新 ${Number(counts.profile_person_ids ?? 0)} 个对象`)
  }
  return items
}

export function formatFeedbackActionType(actionType: string): string {
  switch (actionType) {
    case 'classification':
      return '判定纠错'
    case 'forget_relation':
      return '撤销旧关系'
    case 'mark_stale_paragraph':
      return '标记旧段落'
    case 'write_correction':
      return '写入纠错'
    case 'rollback_restore_relation':
      return '恢复旧关系'
    case 'rollback_delete_correction_paragraph':
      return '隐藏纠错段落'
    case 'rollback_revert_corrected_relation':
      return '撤销纠正关系'
    case 'rollback_clear_stale_mark':
      return '清除脏段落标记'
    case 'rollback_enqueue_episode_rebuild':
      return '加入 Episode 修复队列'
    case 'rollback_enqueue_profile_refresh':
      return '加入 Profile 刷新队列'
    case 'rollback_error':
      return '回退失败'
    case 'error':
      return '处理失败'
    case 'skip':
      return '跳过处理'
    default:
      return actionType || '未知动作'
  }
}

export function describeFeedbackActionLog(item: MemoryFeedbackActionLogPayload): string {
  const beforeSummary = summarizeFeedbackActionPayload(item.before_payload)
  const afterSummary = summarizeFeedbackActionPayload(item.after_payload)

  switch (item.action_type) {
    case 'classification':
      return afterSummary ? `系统完成判定：${afterSummary}` : '系统完成纠错判定'
    case 'forget_relation':
      return beforeSummary ? `旧关系已失效：${beforeSummary}` : '旧关系已被标记为失效'
    case 'mark_stale_paragraph':
      return '旧段落已标记为待复核，后续检索会更谨慎地使用它'
    case 'write_correction':
      return afterSummary ? `已写入新的纠错结果：${afterSummary}` : '已写入新的纠错段落和关系'
    case 'rollback_restore_relation':
      return afterSummary ? `已恢复旧关系状态：${afterSummary}` : '已恢复旧关系状态'
    case 'rollback_delete_correction_paragraph':
      return '已隐藏这次纠错写入的段落'
    case 'rollback_revert_corrected_relation':
      return '已撤销纠错阶段新增的关系'
    case 'rollback_clear_stale_mark':
      return '已清除旧段落的待复核标记'
    case 'rollback_enqueue_episode_rebuild':
      return '已重新加入 Episode 修复队列'
    case 'rollback_enqueue_profile_refresh':
      return '已重新加入 Profile 刷新队列'
    case 'rollback_error':
      return item.reason || '这次回退执行失败'
    case 'error':
      return item.reason || '这次纠错处理失败'
    case 'skip':
      return item.reason || '这次纠错被跳过'
    default:
      return afterSummary || beforeSummary || item.reason || '记录了一条动作日志'
  }
}
