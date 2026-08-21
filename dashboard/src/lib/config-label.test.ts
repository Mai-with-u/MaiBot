import { describe, expect, it } from 'vitest'

import type { FieldSchema } from '@/types/config-schema'

import {
  getAllLocalizedText,
  resolveFieldLabel,
  resolveLocalizedText,
} from './config-label'

function makeField(overrides: Partial<FieldSchema> & Pick<FieldSchema, 'name' | 'label'>): FieldSchema {
  return {
    type: 'string',
    description: '',
    required: false,
    ...overrides,
  }
}

describe('resolveLocalizedText', () => {
  it('缺少文案或空字符串时返回 fallback', () => {
    expect(resolveLocalizedText(undefined, 'zh', '默认')).toBe('默认')
    expect(resolveLocalizedText('', 'zh', '默认')).toBe('默认')
    expect(resolveLocalizedText(undefined)).toBe('')
  })

  it('普通字符串原样返回，空字符串才回落到 fallback', () => {
    expect(resolveLocalizedText('固定标题', 'en', '默认')).toBe('固定标题')
  })

  it.each([
    ['zh', { zh_CN: '中文简体' }, '中文简体'],
    ['zh-CN', { zh: '中文' }, '中文'],
    ['en', { en_US: 'English US' }, 'English US'],
    ['en-US', { 'en-US': 'English hyphen' }, 'English hyphen'],
    ['ja', { ja_JP: '日本語' }, '日本語'],
    ['ja-JP', { 'ja-JP': '日本語ハイフン' }, '日本語ハイフン'],
    ['ko', { 'ko-KR': '한국어' }, '한국어'],
    ['ko_KR', { ko: '한국어축약' }, '한국어축약'],
  ] as const)('语言 %s 会展开别名并命中对应文案', (language, text, expected) => {
    expect(resolveLocalizedText(text, language, 'fallback')).toBe(expected)
  })

  it('未指定语言时按中文候选查找，而不是直接取对象第一个值', () => {
    expect(
      resolveLocalizedText(
        { en: 'English first', zh_CN: '中文配置' },
        undefined,
        'fallback'
      )
    ).toBe('中文配置')
  })

  it('未知语言先走中文兜底，再取对象里第一个非空值', () => {
    expect(resolveLocalizedText({ zh_CN: '中文兜底', fr: 'Bonjour' }, 'de', 'fallback')).toBe(
      '中文兜底'
    )
    expect(resolveLocalizedText({ fr: 'Bonjour', de: '' }, 'it', 'fallback')).toBe('Bonjour')
  })

  it('候选键对应空字符串会被跳过，全部为空则返回 fallback', () => {
    expect(resolveLocalizedText({ en: '', en_US: 'Hello' }, 'en', 'fallback')).toBe('Hello')
    expect(resolveLocalizedText({ en: '', zh_CN: '', zh: '' }, 'en', '缺省')).toBe('缺省')
  })
})

describe('getAllLocalizedText', () => {
  it('缺少文案时返回空数组', () => {
    expect(getAllLocalizedText(undefined)).toEqual([])
    expect(getAllLocalizedText('')).toEqual([])
  })

  it('字符串文案包成单元素数组，对象文案展开全部取值', () => {
    expect(getAllLocalizedText('只读标签')).toEqual(['只读标签'])
    expect(getAllLocalizedText({ zh_CN: '中文', en: 'English' })).toEqual(['中文', 'English'])
  })
})

describe('resolveFieldLabel', () => {
  it('按当前语言解析字段 label，缺失时回落到字段名', () => {
    const field = makeField({
      name: 'reply_timing',
      label: { zh_CN: '回复时机', en: 'Reply timing' },
    })

    expect(resolveFieldLabel(field, 'en')).toBe('Reply timing')
    expect(resolveFieldLabel(field, 'zh')).toBe('回复时机')
    expect(resolveFieldLabel(makeField({ name: 'reply_timing', label: {} }), 'en')).toBe(
      'reply_timing'
    )
  })
})
