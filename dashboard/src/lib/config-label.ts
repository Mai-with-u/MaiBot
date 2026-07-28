import type { FieldSchema, LocalizedText } from '@/types/config-schema'

const LANGUAGE_ALIASES: Record<string, string[]> = {
  zh: ['zh_CN', 'zh-CN', 'zh'],
  en: ['en_US', 'en-US', 'en'],
  ja: ['ja_JP', 'ja-JP', 'ja'],
  ko: ['ko_KR', 'ko-KR', 'ko'],
}

function getLanguageCandidates(language?: string) {
  const normalized = (language || '').replace('-', '_')
  const baseLanguage = normalized.split('_')[0]
  return [
    normalized,
    language || '',
    ...(LANGUAGE_ALIASES[baseLanguage] ?? []),
    'zh_CN',
    'zh-CN',
    'zh',
  ].filter(Boolean)
}

export function resolveLocalizedText(
  text: LocalizedText | undefined,
  language?: string,
  fallback = ''
): string {
  if (!text) {
    return fallback
  }

  if (typeof text === 'string') {
    return text || fallback
  }

  for (const key of getLanguageCandidates(language)) {
    const value = text[key]
    if (value) {
      return value
    }
  }

  return Object.values(text).find(Boolean) ?? fallback
}

export function getAllLocalizedText(text: LocalizedText | undefined): string[] {
  if (!text) {
    return []
  }
  return typeof text === 'string' ? [text] : Object.values(text)
}

export function resolveFieldLabel(field: FieldSchema, language?: string): string {
  return resolveLocalizedText(field.label, language, field.name)
}
