import { backendApi } from '@/lib/http'
import { importExpressions } from '@/lib/expression-api'
import type { JargonConflictAction, JargonImportConflict, JargonImportRequest } from '@/types/jargon'
import { importJargons, previewJargonImport } from '@/lib/jargon-api'

export type LearningKind = 'expression' | 'jargon'

export interface LearnedCandidate {
  title: string
  description: string
  evidence?: string
}

export async function prepareLearningText(text: string): Promise<string[]> {
  const result = await backendApi.post<{ chunks: string[] }>('/api/webui/learning/text/prepare', {
    body: { text },
    errorMessage: '文段分段失败',
  })
  return result.chunks
}

export async function extractTextCandidates(
  kind: LearningKind,
  text: string
): Promise<LearnedCandidate[]> {
  const result = await backendApi.post<{ candidates: LearnedCandidate[] }>(
    '/api/webui/learning/text/extract',
    {
      body: { kind, text },
      errorMessage: '文段学习失败',
    }
  )
  return result.candidates
}

/** 跨段去重，保持候选顺序和勾选项稳定；同一黑话只展示一次。 */
export function mergeLearningCandidates(
  current: LearnedCandidate[],
  incoming: LearnedCandidate[],
  kind: LearningKind
): LearnedCandidate[] {
  const keyOf = (candidate: LearnedCandidate) =>
    JSON.stringify(
      kind === 'jargon'
        ? [candidate.title.trim().toLocaleLowerCase()]
        : [candidate.title.trim(), candidate.description.trim()]
    )
  const seen = new Set(current.map(keyOf))
  return [
    ...current,
    ...incoming.filter((candidate) => {
      const key = keyOf(candidate)
      if (seen.has(key)) return false
      seen.add(key)
      return true
    }),
  ]
}

/** 仅导入用户明确勾选的候选，冲突沿用现有导入接口的跳过策略。 */
export async function importLearningCandidates(
  kind: LearningKind,
  targetIds: string[],
  candidates: LearnedCandidate[],
  isGlobal = false,
  reviewConflict?: (
    conflict: JargonImportConflict, meaning: string, remaining: number
  ) => Promise<{ action: JargonConflictAction; applyToAll: boolean }>,
  lockMeaning = true
) {
  if ((!isGlobal && !targetIds.length) || !candidates.length)
    throw new Error('请选择导入目标和候选')
  if (isGlobal && targetIds.length) throw new Error('全局导入不能同时指定聊天')
  const items = candidates.map((candidate) => ({
    ...candidate,
    title: candidate.title.trim(),
    description: candidate.description.trim(),
    evidence: candidate.evidence?.trim(),
  }))
  if (items.some((item) => !item.title || !item.description))
    throw new Error('请填写所选结果的内容和含义或表达')
  if (kind === 'expression') {
    if (!isGlobal && targetIds.length !== 1) throw new Error('表达方式请选择一个目标聊天')
    return importExpressions({
      ...(isGlobal ? { is_global: true } : { chat_id: targetIds[0] }),
      expressions: items.map((candidate) => ({
        situation: candidate.title,
        style: candidate.description,
        content_list: JSON.stringify(candidate.evidence ? [candidate.evidence] : []),
        count: 1,
        last_active_time: null,
        create_time: null,
        checked: true,
        modified_by: 'user',
      })),
    })
  }
  const request: JargonImportRequest = {
    target_session_ids: targetIds,
    ...(isGlobal ? { is_global: true } : {}),
    conflict_strategy: 'skip',
    jargons: items.map((candidate) => ({
      content: candidate.title,
      excerpt: candidate.evidence,
      meaning: candidate.description,
      count: 1,
      is_jargon: true,
      allow_update: !lockMeaning,
      is_complete: false,
      is_global: isGlobal,
      created_by: 'MANUAL',
    })),
  }
  if (reviewConflict) {
    const meanings = new Map(items.map((item) => [item.title, item.description]))
    if (meanings.size !== items.length) throw new Error('所选候选包含同名词条，请修改后再导入')
    const conflicts = await previewJargonImport(request)
    const replacements = new Map<string, JargonImportConflict['records']>()
    const merges = new Map<string, JargonImportConflict['records']>()
    const skips: string[] = []
    let batchDecision: JargonConflictAction | undefined
    for (const [index, conflict] of conflicts.entries()) {
      let action: JargonConflictAction | undefined = batchDecision
      if (action === undefined) {
        const decision = await reviewConflict(conflict, meanings.get(conflict.content)!, conflicts.length - index)
        action = decision.action
        if (decision.applyToAll) batchDecision = action
      }
      if (action === 'merge') merges.set(conflict.content, conflict.records)
      else if (action === 'replace') replacements.set(conflict.content, conflict.records)
      else skips.push(conflict.content)
    }
    request.replacements = Object.fromEntries(replacements)
    if (merges.size) request.merges = Object.fromEntries(merges)
    request.reviewed_skips = skips
  }
  return importJargons(request)
}
