import type {
  MemoryProfileEvidenceItemPayload,
  MemoryProfileItemPayload,
  MemoryProfileQueryPayload,
} from '@/lib/memory-api'

export function formatMemoryTime(timestamp?: number | null): string {
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

export function parseAliasText(value: string): string[] {
  const aliases: string[] = []
  const seen = new Set<string>()
  for (const item of value.split(/[\n,，]/)) {
    const alias = item.trim()
    const key = alias.toLocaleLowerCase()
    if (!alias || seen.has(key)) {
      continue
    }
    seen.add(key)
    aliases.push(alias)
  }
  return aliases
}

export function parsePositiveInt(value: string, fallback: number): number {
  const parsed = Number(value)
  if (!Number.isInteger(parsed) || parsed <= 0) {
    return fallback
  }
  return parsed
}

export function stringifyOverride(value: MemoryProfileItemPayload['manual_override']): string {
  if (!value) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  const text = value.override_text ?? value.text
  if (typeof text === 'string') {
    return text
  }
  return JSON.stringify(value, null, 2)
}

export function resolveProfileText(
  queryResult: MemoryProfileQueryPayload | null,
  selectedProfile: MemoryProfileItemPayload | null
): string {
  if (typeof queryResult?.profile_text === 'string') {
    return queryResult.profile_text
  }
  const queryProfile = queryResult?.profile
  if (
    queryProfile &&
    typeof queryProfile === 'object' &&
    typeof queryProfile.profile_text === 'string'
  ) {
    return queryProfile.profile_text
  }
  return selectedProfile?.profile_text ?? ''
}

export function evidenceTypeLabel(type?: string): string {
  switch (type) {
    case 'relation':
      return '关系'
    case 'person_fact':
      return '人物事实'
    case 'chat_summary':
      return '聊天摘要'
    case 'paragraph':
      return '段落'
    default:
      return type || '未知'
  }
}

export function formatEvidenceScore(item: MemoryProfileEvidenceItemPayload): string {
  const confidence = Number(item.confidence)
  if (Number.isFinite(confidence)) {
    return `置信度 ${confidence.toFixed(2)}`
  }
  const score = Number(item.score)
  if (Number.isFinite(score)) {
    return `分数 ${score.toFixed(2)}`
  }
  return '-'
}

/**
 * 人物画像候选行的身份补充文案：昵称与群名片。
 * 后端把 PersonInfo 的可读身份字段一起返回，这里只负责去重与拼接，供列表行直接展示与检索对照。
 */
export function describeProfileIdentity(item: MemoryProfileItemPayload): string[] {
  const nickname = String(item.user_nickname ?? '').trim()
  const personName = String(item.person_name ?? '').trim()
  const cardnames = (item.group_cardname_list ?? [])
    .map((name) => String(name ?? '').trim())
    .filter(Boolean)

  const labels: string[] = []
  // person_name 已经作为主标题展示，这里不再重复
  if (nickname && nickname !== personName) {
    labels.push(`昵称 ${nickname}`)
  }
  for (const cardname of cardnames) {
    labels.push(`群名片 ${cardname}`)
  }
  return labels
}
