/**
 * 学习内容跨类别「手动导入」：识别文件归属的类别，再分别调用三类已有的导入接口。
 *
 * - 表达方式：本页导出的 JSON（type = maibot.expression.export），或裸数组 / { expressions }
 * - 黑话：本页导出的 JSON（type = maibot.jargon.export），或 { jargons }
 * - 表情包：png / jpg / gif / webp 图片
 */
import { getEmojiUploadUrl } from '@/lib/emoji-api'
import { importExpressions } from '@/lib/expression-api'
import { backendApi } from '@/lib/http'
import { importJargons } from '@/lib/jargon-api'

import type { ExpressionExportItem } from '@/types/expression'
import type { JargonExportItem } from '@/types/jargon'

export type LearningKind = 'expression' | 'jargon' | 'emoji'

export const EMOJI_MIME_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']

export interface LearningImportBundle {
  expressions: ExpressionExportItem[]
  jargons: JargonExportItem[]
  emojis: File[]
  /** 已识别的来源文件名，按类别列出 */
  sources: Record<LearningKind, string[]>
  /** 无法识别或解析失败的文件 */
  rejected: { name: string; reason: string }[]
}

export function emptyBundle(): LearningImportBundle {
  return {
    expressions: [],
    jargons: [],
    emojis: [],
    sources: { expression: [], jargon: [], emoji: [] },
    rejected: [],
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 根据 JSON 内容判断类别；无法判断时返回 null。 */
export function classifyLearningJson(
  payload: unknown
):
  | { kind: 'expression'; items: ExpressionExportItem[] }
  | { kind: 'jargon'; items: JargonExportItem[] }
  | null {
  if (Array.isArray(payload)) {
    const first = payload[0]
    if (isRecord(first) && 'situation' in first && 'style' in first) {
      return { kind: 'expression', items: payload as ExpressionExportItem[] }
    }
    if (isRecord(first) && 'content' in first) {
      return { kind: 'jargon', items: payload as JargonExportItem[] }
    }
    return null
  }
  if (!isRecord(payload)) return null
  const type = typeof payload.type === 'string' ? payload.type : ''
  if (type === 'maibot.expression.export' || Array.isArray(payload.expressions)) {
    return Array.isArray(payload.expressions)
      ? { kind: 'expression', items: payload.expressions as ExpressionExportItem[] }
      : null
  }
  if (type === 'maibot.jargon.export' || Array.isArray(payload.jargons)) {
    return Array.isArray(payload.jargons)
      ? { kind: 'jargon', items: payload.jargons as JargonExportItem[] }
      : null
  }
  return null
}

function isEmojiFile(file: File): boolean {
  if (EMOJI_MIME_TYPES.includes(file.type)) return true
  return /\.(png|jpe?g|gif|webp)$/i.test(file.name)
}

/** 读取用户选的文件，把内容归到三类里，合并进已有的 bundle。 */
export async function readLearningFiles(
  files: File[],
  base: LearningImportBundle = emptyBundle()
): Promise<LearningImportBundle> {
  const next: LearningImportBundle = {
    expressions: [...base.expressions],
    jargons: [...base.jargons],
    emojis: [...base.emojis],
    sources: {
      expression: [...base.sources.expression],
      jargon: [...base.sources.jargon],
      emoji: [...base.sources.emoji],
    },
    rejected: [...base.rejected],
  }
  for (const file of files) {
    if (isEmojiFile(file)) {
      next.emojis.push(file)
      next.sources.emoji.push(file.name)
      continue
    }
    if (!/\.json$/i.test(file.name) && file.type !== 'application/json') {
      next.rejected.push({ name: file.name, reason: '不支持的文件类型' })
      continue
    }
    let payload: unknown
    try {
      payload = JSON.parse(await file.text())
    } catch {
      next.rejected.push({ name: file.name, reason: '不是有效的 JSON' })
      continue
    }
    const result = classifyLearningJson(payload)
    if (!result || result.items.length === 0) {
      next.rejected.push({ name: file.name, reason: '没有识别到表达方式或黑话' })
      continue
    }
    if (result.kind === 'expression') next.expressions.push(...result.items)
    else next.jargons.push(...result.items)
    next.sources[result.kind].push(file.name)
  }
  return next
}

export interface LearningImportOptions {
  kinds: Record<LearningKind, boolean>
  /** null 表示全局（所有聊天）；否则导入到这些聊天 */
  chatIds: string[] | null
  /** 黑话同名时的处理；表达方式始终跳过完全相同的条目，表情包按文件内容去重 */
  conflict: 'skip' | 'overwrite'
  /** 给本次导入的表情包统一加的情感标签，可留空 */
  emojiEmotion: string
}

export interface LearningImportResult {
  kind: LearningKind
  imported: number
  skipped: number
  failed: number
  error?: string
}

/** 按类别依次调用已有接口；某一类失败不影响其他类。 */
export async function runLearningImport(
  bundle: LearningImportBundle,
  options: LearningImportOptions
): Promise<LearningImportResult[]> {
  const results: LearningImportResult[] = []
  const chatIds = options.chatIds
  const isGlobal = chatIds === null

  if (options.kinds.expression && bundle.expressions.length > 0) {
    // 表达方式接口一次只收一个聊天，多个聊天逐个导入后合计。
    const total: LearningImportResult = { kind: 'expression', imported: 0, skipped: 0, failed: 0 }
    const requests = isGlobal
      ? [{ is_global: true, expressions: bundle.expressions }]
      : chatIds.map((chat_id) => ({ chat_id, expressions: bundle.expressions }))
    for (const request of requests) {
      try {
        const response = await importExpressions(request)
        total.imported += response.imported_count
        total.skipped += response.skipped_count
        total.failed += response.failed_count
      } catch (error) {
        total.failed += bundle.expressions.length
        total.error = errorText(error)
      }
    }
    results.push(total)
  }

  if (options.kinds.jargon && bundle.jargons.length > 0) {
    try {
      const response = await importJargons({
        target_session_ids: isGlobal ? [] : chatIds,
        is_global: isGlobal,
        jargons: bundle.jargons,
        conflict_strategy: options.conflict,
      })
      results.push({
        kind: 'jargon',
        imported: response.imported_count,
        skipped: response.skipped_count,
        failed: response.failed_count,
      })
    } catch (error) {
      results.push({
        kind: 'jargon',
        imported: 0,
        skipped: 0,
        failed: bundle.jargons.length,
        error: errorText(error),
      })
    }
  }

  if (options.kinds.emoji && bundle.emojis.length > 0) {
    let imported = 0
    let failed = 0
    let lastError: string | undefined
    for (const file of bundle.emojis) {
      const formData = new FormData()
      formData.append('file', file)
      if (options.emojiEmotion.trim()) formData.append('emotion', options.emojiEmotion.trim())
      try {
        await backendApi.post(getEmojiUploadUrl(), {
          body: formData,
          parse: 'response',
          errorMessage: '上传表情包失败',
        })
        imported++
      } catch (error) {
        failed++
        lastError = errorText(error)
      }
    }
    results.push({ kind: 'emoji', imported, skipped: 0, failed, error: lastError })
  }

  return results
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
